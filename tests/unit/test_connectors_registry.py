"""The registry: what is configured, and the tables derived from the descriptors."""

from __future__ import annotations

import pytest

from yeaboi.connectors import registry
from yeaboi.connectors.datadog import CONNECTOR as DATADOG

#: The verify table as it stood before it was derived. Frozen here so the
#: derivation is checked against the literal it replaced rather than against
#: itself — including FIELD ORDER, which verify_connection iterates.
LEGACY_KINDS = {
    "github": (("token", "GITHUB_TOKEN"),),
    "jira": (("base_url", "JIRA_BASE_URL"), ("email", "JIRA_EMAIL"), ("token", "JIRA_API_TOKEN")),
    # NOTE: Confluence verifies against JIRA_* deliberately-as-shipped, not
    # against CONFLUENCE_BASE_URL/EMAIL/API_TOKEN — which config.py DOES read.
    # That gap predates the connector layer and is not fixed here; this literal
    # freezes the behaviour as it stands so the derivation cannot change it by
    # accident while it is being fixed on its own.
    "confluence": (
        ("base_url", "JIRA_BASE_URL"),
        ("email", "JIRA_EMAIL"),
        ("token", "JIRA_API_TOKEN"),
        ("space_key", "CONFLUENCE_SPACE_KEY"),
    ),
    "notion": (("token", "NOTION_TOKEN"),),
    "elevenlabs": (("token", "ELEVENLABS_API_KEY"),),
    "tavus": (("token", "TAVUS_API_KEY"),),
}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for env in registry.all_envs():
        monkeypatch.delenv(env, raising=False)


class TestConnected:
    def test_nothing_is_connected_on_a_bare_machine(self):
        assert registry.connected() == []

    def test_all_required_envs_must_be_present(self, monkeypatch):
        monkeypatch.setenv("DATADOG_API_KEY", "k")
        assert registry.connected() == [], "one of two credentials is not a connection"
        monkeypatch.setenv("DATADOG_APP_KEY", "a")
        assert registry.connected() == ["datadog"]

    def test_an_optional_field_does_not_gate_it(self, monkeypatch):
        monkeypatch.setenv("DATADOG_API_KEY", "k")
        monkeypatch.setenv("DATADOG_APP_KEY", "a")
        assert "datadog" in registry.connected()
        assert not __import__("os").environ.get("DATADOG_SITE")

    def test_whitespace_is_not_a_credential(self, monkeypatch):
        monkeypatch.setenv("DATADOG_API_KEY", "   ")
        monkeypatch.setenv("DATADOG_APP_KEY", "a")
        assert registry.connected() == []

    def test_family_filter(self, monkeypatch):
        monkeypatch.setenv("DATADOG_API_KEY", "k")
        monkeypatch.setenv("DATADOG_APP_KEY", "a")
        assert registry.connected("observability") == ["datadog"]
        assert registry.connected("incidents") == []


class TestDerivedTables:
    def test_connection_kinds_reproduces_the_legacy_literals(self):
        from yeaboi.settings.engine import _connection_kinds

        merged = _connection_kinds()
        for kind, spec in LEGACY_KINDS.items():
            assert merged[kind] == spec, f"{kind}'s verify spec drifted from the literal it replaced"

    def test_datadog_never_accepts_a_caller_supplied_site(self):
        # A supplied site paired with the stored token would send that token to
        # a host of the caller's choosing — the exfiltration the base_url guard
        # exists to stop.
        spec = registry.connection_kinds()["datadog"]
        assert [name for name, _env in spec] == ["token", "app_key"]

    def test_secret_envs_are_the_descriptors(self):
        assert registry.secret_envs() == {env for c in registry.all_connectors() for env in c.secret_envs}
        assert "DATADOG_API_KEY" in registry.secret_envs()

    def test_all_envs_preserves_descriptor_order(self):
        assert registry.all_envs()[:3] == ("DATADOG_API_KEY", "DATADOG_APP_KEY", "DATADOG_SITE")

    def test_accents_match_the_web_contract(self):
        import json
        import pathlib

        contract = json.loads(
            (pathlib.Path(__file__).resolve().parents[2] / "contracts" / "web" / "ui.json").read_text()
        )
        assert sorted(registry.accents()) == contract["connector_accents"], (
            "connector identities and contracts/web/ui.json disagree — run `make web-types`"
        )


class TestLookup:
    def test_by_key(self):
        assert registry.by_key("datadog") is DATADOG
        assert registry.by_key("nope") is None


class TestTheDispatchIsDerived:
    """Stage 2's real test: four vendors, and no surface code changed.

    ``verify_connection`` used to name each connector in an if/elif chain — the
    tenth registry the descriptor layer exists to delete. These assert it now
    reads the descriptor instead, for every connector at once.
    """

    def test_every_connector_is_verifiable_without_a_dispatch_branch(self):
        import inspect

        from yeaboi.settings import engine

        source = inspect.getsource(engine.verify_connection)
        for c in registry.all_connectors():
            assert f'kind == "{c.key}"' not in source, f"{c.key} is hand-written into the dispatch"
            assert c.key in engine._connection_kinds(), f"{c.key} is not verifiable"

    def test_the_probe_receives_request_fields_and_saved_hosts(self, monkeypatch):
        from yeaboi.connectors.datadog import CONNECTOR
        from yeaboi.settings import engine

        seen = {}
        monkeypatch.setattr(
            "yeaboi.provider_verification._verify_datadog",
            lambda **kw: (seen.update(kw), (True, "ok"))[1],
        )
        monkeypatch.setenv("DATADOG_SITE", "datadoghq.eu")
        engine.verify_connection(CONNECTOR.key, {"token": "t", "app_key": "a"})
        assert seen == {"token": "t", "app_key": "a", "site": "datadoghq.eu"}

    def test_a_request_cannot_set_a_host_field(self, monkeypatch):
        # The exfiltration guard end to end: a site in the request body is
        # ignored, and the saved one is used instead.
        from yeaboi.settings import engine

        seen = {}
        monkeypatch.setattr(
            "yeaboi.provider_verification._verify_datadog",
            lambda **kw: (seen.update(kw), (True, "ok"))[1],
        )
        monkeypatch.setenv("DATADOG_SITE", "datadoghq.com")
        engine.verify_connection("datadog", {"token": "t", "app_key": "a", "site": "attacker.example.com"})
        assert seen["site"] == "datadoghq.com"
