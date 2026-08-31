"""The catalog payload — what every surface reads, and what it must never carry."""

from __future__ import annotations

import pytest

from yeaboi.connectors import registry
from yeaboi.connectors.engine import list_connections

API_KEY = "dd-api-key-should-never-appear"
APP_KEY = "dd-app-key-should-never-appear"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for env in registry.all_envs():
        monkeypatch.delenv(env, raising=False)


@pytest.fixture
def _connected(monkeypatch):
    monkeypatch.setenv("DATADOG_API_KEY", API_KEY)
    monkeypatch.setenv("DATADOG_APP_KEY", APP_KEY)
    monkeypatch.setenv("DATADOG_SITE", "datadoghq.eu")


class TestHiddenUntilConnected:
    def test_the_default_lists_nothing_on_a_bare_machine(self):
        payload = list_connections()
        assert payload["connectors"] == []
        assert payload["families"] == []
        assert payload["connected"] == []

    def test_connected_only_is_the_default(self):
        # This default IS "hidden until connected" — a caller that forgets the
        # argument must get the quiet answer, not the catalog.
        assert list_connections() == list_connections(connected_only=True)

    def test_the_picker_can_ask_for_everything(self):
        from yeaboi.connectors import registry

        payload = list_connections(connected_only=False)
        assert [c["key"] for c in payload["connectors"]] == [c.key for c in registry.all_connectors()]
        assert all(c["connected"] is False for c in payload["connectors"])

    def test_a_connected_vendor_appears(self, _connected):
        payload = list_connections()
        assert [c["key"] for c in payload["connectors"]] == ["datadog"]
        assert payload["connectors"][0]["connected"] is True
        assert payload["connected"] == ["datadog"]


class TestNoCredentialEverLeaves:
    def test_no_field_value_appears_anywhere_in_the_payload(self, _connected):
        payload = list_connections(connected_only=False)
        rendered = repr(payload)
        assert API_KEY not in rendered
        assert APP_KEY not in rendered

    def test_a_field_reports_only_whether_it_is_set(self, _connected):
        fields = {f["env"]: f for f in list_connections()["connectors"][0]["fields"]}
        assert fields["DATADOG_API_KEY"]["is_set"] is True
        assert fields["DATADOG_SITE"]["is_set"] is True
        assert "value" not in fields["DATADOG_API_KEY"]

    def test_an_unset_field_says_so(self, monkeypatch):
        monkeypatch.setenv("DATADOG_API_KEY", API_KEY)
        monkeypatch.setenv("DATADOG_APP_KEY", APP_KEY)
        fields = {f["env"]: f for f in list_connections()["connectors"][0]["fields"]}
        assert fields["DATADOG_SITE"]["is_set"] is False


class TestShape:
    def test_a_row_carries_its_identity(self, _connected):
        row = list_connections()["connectors"][0]
        assert row["glyph"]
        assert row["accent"].startswith("rgb(")
        assert row["family_label"] == "Observability"
        assert row["read_only"] is True
        assert row["verify_kind"] == "datadog"

    def test_families_only_name_families_present_in_the_rows(self, _connected):
        payload = list_connections()
        assert [f["key"] for f in payload["families"]] == ["observability"]

    def test_family_filter_narrows_both_halves(self, _connected):
        assert list_connections(family="incidents")["connectors"] == []
        assert list_connections(family="observability")["connectors"]

    def test_the_payload_is_json_serialisable(self, _connected):
        import json

        json.dumps(list_connections(connected_only=False))
