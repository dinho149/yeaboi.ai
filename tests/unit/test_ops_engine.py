"""``fetch_ops_events`` — the read side of the connections capability.

What it gathers, what it refuses to gather, and the two things it must never
put in a payload: a credential, and a source the user has not connected.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from yeaboi.connectors.engine import fetch_ops_events
from yeaboi.connectors.fetching import FetchError
from yeaboi.ops.events import OpsEvent

NOW = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)


def event(**kw) -> OpsEvent:
    base = {
        "kind": "incident",
        "source": "pagerduty",
        "ref": "PD-1",
        "title": "checkout down",
        "started_at": "2026-06-10T09:00:00Z",
    }
    return OpsEvent(**{**base, **kw})


@pytest.fixture
def pd(monkeypatch):
    """PagerDuty connected, with its fetcher replaced by a stub."""
    monkeypatch.setenv("PAGERDUTY_API_KEY", "pd-tok")
    calls = []

    def fake(start, end):
        calls.append((start, end))
        return (event(),)

    monkeypatch.setattr("yeaboi.connectors.pagerduty.fetch", fake)
    return calls


@pytest.fixture(autouse=True)
def _nothing_else_connected(monkeypatch):
    for env in (
        "DATADOG_API_KEY",
        "DATADOG_APP_KEY",
        "GRAFANA_BASE_URL",
        "GRAFANA_API_TOKEN",
        "PAGERDUTY_API_KEY",
        "INCIDENTIO_API_KEY",
        "SENTRY_AUTH_TOKEN",
        "SENTRY_ORG",
    ):
        monkeypatch.delenv(env, raising=False)


class TestHiddenUntilConnected:
    def test_nothing_connected_gathers_nothing_and_names_nobody(self):
        # The same rule the catalog follows: an unconnected vendor cannot appear
        # in a result, so no renderer downstream can name it.
        payload = fetch_ops_events(since="14d", now=NOW)
        assert payload["sources"] == []
        assert payload["events"] == []
        assert payload["signals"] == []

    def test_only_connected_sources_are_gathered(self, pd):
        payload = fetch_ops_events(since="14d", now=NOW)
        assert [s["key"] for s in payload["sources"]] == ["pagerduty"]

    def test_naming_an_unconnected_one_says_so_rather_than_failing_silently(self):
        payload = fetch_ops_events("pagerduty", since="14d", now=NOW)
        assert payload["sources"][0]["ok"] is False
        assert "not connected" in payload["sources"][0]["error"]


class TestTheWindow:
    def test_it_travels_with_the_result(self, pd):
        payload = fetch_ops_events(since="14d", now=NOW)
        assert payload["window"]["since"] == "14d"
        assert payload["window"]["start"].startswith("2026-06-01")
        assert payload["window"]["end"].startswith("2026-06-15")

    def test_the_fetcher_is_handed_the_same_window(self, pd):
        fetch_ops_events(since="7d", now=NOW)
        start, end = pd[0]
        assert (end - start).days == 7 and end == NOW

    def test_an_event_outside_the_window_is_dropped(self, monkeypatch):
        # The engine re-checks rather than trusting a vendor's own filtering.
        monkeypatch.setenv("PAGERDUTY_API_KEY", "pd-tok")
        monkeypatch.setattr(
            "yeaboi.connectors.pagerduty.fetch",
            lambda s, e: (event(), event(ref="PD-old", started_at="2020-01-01T00:00:00Z")),
        )
        payload = fetch_ops_events(since="14d", now=NOW)
        assert [e["ref"] for e in payload["events"]] == ["PD-1"]
        assert payload["sources"][0]["count"] == 1

    def test_a_bad_window_is_a_value_error_before_any_request(self, pd):
        with pytest.raises(ValueError, match="invalid window"):
            fetch_ops_events(since="forever", now=NOW)
        assert pd == []


class TestOneVendorDownDoesNotLoseTheRest:
    def test_a_failing_source_is_reported_not_raised(self, monkeypatch):
        monkeypatch.setenv("PAGERDUTY_API_KEY", "pd-tok")
        monkeypatch.setenv("INCIDENTIO_API_KEY", "inc-tok")

        def boom(start, end):
            raise FetchError("pagerduty: rate limited")

        monkeypatch.setattr("yeaboi.connectors.pagerduty.fetch", boom)
        monkeypatch.setattr("yeaboi.connectors.incidentio.fetch", lambda s, e: (event(source="incidentio"),))

        payload = fetch_ops_events(since="14d", now=NOW)
        by_key = {s["key"]: s for s in payload["sources"]}
        assert by_key["pagerduty"]["ok"] is False and "rate limited" in by_key["pagerduty"]["error"]
        assert by_key["incidentio"]["ok"] is True
        assert len(payload["events"]) == 1

    def test_an_unexpected_exception_is_redacted_not_trusted(self, monkeypatch):
        monkeypatch.setenv("PAGERDUTY_API_KEY", "pd-tok")

        def boom(start, end):
            raise RuntimeError("connecting with token pd-tok")

        monkeypatch.setattr("yeaboi.connectors.pagerduty.fetch", boom)
        payload = fetch_ops_events(since="14d", now=NOW)
        assert payload["sources"][0]["ok"] is False


class TestThePayload:
    def test_carries_no_credential(self, monkeypatch, pd):
        import json

        payload = fetch_ops_events(since="14d", now=NOW)
        assert "pd-tok" not in json.dumps(payload)

    def test_rolls_up_alongside_the_raw_events(self, pd):
        payload = fetch_ops_events(since="14d", now=NOW)
        assert payload["events"][0]["ref"] == "PD-1"
        assert payload["signals"][0]["count"] == 1
        # The family is resolved from the registry, so a signal can be grouped
        # without every caller re-deriving it.
        assert payload["signals"][0]["family"] == "incidents"

    def test_the_event_rows_carry_exactly_the_dataclass_fields(self, pd):
        import dataclasses

        payload = fetch_ops_events(since="14d", now=NOW)
        assert set(payload["events"][0]) == {f.name for f in dataclasses.fields(OpsEvent)}

    def test_it_is_json_serialisable(self, pd):
        import json

        json.dumps(fetch_ops_events(since="14d", now=NOW))


class TestUnknownAndUngatherable:
    def test_an_unknown_key_is_rejected(self):
        with pytest.raises(ValueError, match="unknown connector"):
            fetch_ops_events("nope", now=NOW)

    def test_a_connector_with_no_fetcher_says_so(self, monkeypatch):
        from yeaboi.connectors import registry
        from yeaboi.connectors.spec import Connector

        bare = Connector(key="bare", label="Bare", family="cloud", section="connections", fields=())
        monkeypatch.setattr(registry, "by_key", lambda key: bare if key == "bare" else None)
        with pytest.raises(ValueError, match="nothing to gather"):
            fetch_ops_events("bare", now=NOW)


def test_every_connector_that_declares_a_fetch_has_one():
    """A typo'd ``fetch`` name is a runtime AttributeError, not a red test."""
    import importlib

    from yeaboi.connectors import registry

    for connector in registry.all_connectors():
        if not connector.fetch:
            continue
        module = importlib.import_module(f"yeaboi.connectors.{connector.key}")
        assert callable(getattr(module, connector.fetch, None)), f"{connector.key}.{connector.fetch} is missing"
