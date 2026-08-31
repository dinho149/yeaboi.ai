"""The three cloud fetchers, and the boundary they must not cross.

Each vendor returns a free-text field that carries real content — AWS's history
data, GCP's representative stack trace, Azure's alert context. The canary in
each fixture below is planted in exactly that field, so a fetcher that starts
reading it fails here rather than putting a stack trace in a standup.
"""

from __future__ import annotations

import sys
import types
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from yeaboi.connectors import aws, azure_cloud, gcp

CANARY = "CANARY-secret-payload-do-not-surface"
START = datetime(2026, 8, 17, tzinfo=timezone.utc)
END = datetime(2026, 8, 31, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    from yeaboi.connectors import registry

    for env in registry.all_envs():
        monkeypatch.delenv(env, raising=False)


# --- AWS ---------------------------------------------------------------------

AWS_HISTORY = {
    "AlarmHistoryItems": [
        {
            "AlarmName": "checkout-5xx",
            "Timestamp": "2026-08-20T10:00:00Z",
            "HistorySummary": "Alarm updated from OK to ALARM",
            "HistoryData": f'{{"newState":{{"stateReason":"{CANARY}"}}}}',
        },
        {
            "AlarmName": "checkout-5xx",
            "Timestamp": "2026-08-20T11:00:00Z",
            "HistorySummary": "Alarm updated from ALARM to OK",
            "HistoryData": CANARY,
        },
        {
            "AlarmName": "queue-depth",
            "Timestamp": "2026-08-25T09:00:00Z",
            "HistorySummary": "Alarm updated from INSUFFICIENT_DATA to ALARM",
            "HistoryData": CANARY,
        },
    ]
}


def _aws_ready(monkeypatch, history=None, raises=None):
    monkeypatch.setenv("AWS_AUTH_METHOD", "ambient")
    monkeypatch.setenv("AWS_CLOUD_REGION", "eu-west-1")
    monkeypatch.setattr("yeaboi.connectors.aws.installed", lambda: True)
    module = types.ModuleType("boto3")

    def client(service, **kw):
        stub = MagicMock()
        if raises:
            stub.describe_alarm_history.side_effect = raises
        else:
            stub.describe_alarm_history.return_value = history if history is not None else AWS_HISTORY
        return stub

    module.client = client
    monkeypatch.setitem(sys.modules, "boto3", module)


class TestAwsFetch:
    def test_only_transitions_into_alarm_count(self, monkeypatch):
        # A recovery is not an incident; counting both would double every alarm.
        _aws_ready(monkeypatch)
        events = aws.fetch(START, END)
        assert [e.title for e in events] == ["checkout-5xx", "queue-depth"]

    def test_each_firing_is_its_own_handle(self, monkeypatch):
        # One alarm firing twice is two events, so the ref carries the timestamp.
        _aws_ready(monkeypatch)
        refs = [e.ref for e in aws.fetch(START, END)]
        assert len(set(refs)) == len(refs)
        assert refs[0].startswith("checkout-5xx@")

    def test_a_missing_extra_is_a_fetch_error_not_an_import_crash(self, monkeypatch):
        from yeaboi.connectors.fetching import FetchError

        monkeypatch.setattr("yeaboi.connectors.aws.installed", lambda: False)
        with pytest.raises(FetchError, match="uv sync --extra cloud"):
            aws.fetch(START, END)

    def test_an_api_failure_is_redacted(self, monkeypatch):
        from yeaboi.connectors.fetching import FetchError

        _aws_ready(monkeypatch, raises=RuntimeError("denied for AKIAIOSFODNN7EXAMPLE"))
        with pytest.raises(FetchError) as caught:
            aws.fetch(START, END)
        assert "AKIAIOSFODNN7EXAMPLE" not in str(caught.value)


# --- GCP ---------------------------------------------------------------------

GCP_GROUPS = {
    "errorGroupStats": [
        {
            "group": {"groupId": "CNSWyoT4uZjfrAE"},
            "count": "5000",
            "firstSeenTime": "2026-08-18T08:00:00Z",
            "lastSeenTime": "2026-08-29T08:00:00Z",
            "affectedServices": [{"service": "checkout"}],
            "representative": {"message": f"ValueError: {CANARY}\\n  at handler.py:42"},
        },
        {
            "group": {"groupId": "OLD"},
            "firstSeenTime": "2026-01-01T00:00:00Z",
            "lastSeenTime": "2026-01-02T00:00:00Z",
            "representative": {"message": CANARY},
        },
    ]
}


class TestGcpFetch:
    def test_one_group_is_one_event_not_one_per_occurrence(self, monkeypatch):
        # The group carries count 5000. "3 error groups affecting checkout" is a
        # claim the data supports; "5,000 errors" invites arithmetic against a
        # baseline this tool does not have.
        monkeypatch.setattr("yeaboi.connectors.gcp.installed", lambda: True)
        monkeypatch.setattr("yeaboi.connectors.gcp.access_token", lambda: "tok")
        monkeypatch.setattr("yeaboi.connectors.fetching.read_json", lambda *a, **kw: GCP_GROUPS)
        monkeypatch.setenv("GCP_PROJECT_ID", "proj")
        events = gcp.fetch(START, END)
        assert len(events) == 1
        assert events[0].kind == "error_spike"

    def test_a_group_outside_the_window_is_dropped(self, monkeypatch):
        monkeypatch.setattr("yeaboi.connectors.gcp.installed", lambda: True)
        monkeypatch.setattr("yeaboi.connectors.gcp.access_token", lambda: "tok")
        monkeypatch.setattr("yeaboi.connectors.fetching.read_json", lambda *a, **kw: GCP_GROUPS)
        monkeypatch.setenv("GCP_PROJECT_ID", "proj")
        assert [e.ref for e in gcp.fetch(START, END)] == ["CNSWyoT4uZjfrAE"]

    def test_it_is_named_by_its_service_never_by_the_trace(self, monkeypatch):
        monkeypatch.setattr("yeaboi.connectors.gcp.installed", lambda: True)
        monkeypatch.setattr("yeaboi.connectors.gcp.access_token", lambda: "tok")
        monkeypatch.setattr("yeaboi.connectors.fetching.read_json", lambda *a, **kw: GCP_GROUPS)
        monkeypatch.setenv("GCP_PROJECT_ID", "proj")
        event = gcp.fetch(START, END)[0]
        assert event.title == "checkout error group"
        assert event.service == "checkout"

    def test_no_project_is_an_actionable_error(self, monkeypatch):
        from yeaboi.connectors.fetching import FetchError

        monkeypatch.setattr("yeaboi.connectors.gcp.installed", lambda: True)
        with pytest.raises(FetchError, match="connections add gcp"):
            gcp.fetch(START, END)

    def test_a_project_id_cannot_escape_its_path_segment(self):
        url = gcp.group_stats_url("../../admin", START, END)
        assert "/projects/..%2F..%2Fadmin/" in url

    def test_the_window_picks_the_narrowest_period_that_covers_it(self):
        from datetime import timedelta

        assert "PERIOD_1_HOUR" in gcp.group_stats_url("p", END - timedelta(minutes=30), END)
        assert "PERIOD_1_WEEK" in gcp.group_stats_url("p", END - timedelta(days=3), END)
        assert "PERIOD_30_DAYS" in gcp.group_stats_url("p", END - timedelta(days=200), END)


# --- Azure -------------------------------------------------------------------

AZURE_ALERTS = {
    "value": [
        {
            "name": "alert-1",
            "id": "/subscriptions/sub/alerts/alert-1",
            "properties": {
                "essentials": {
                    "alertRule": "checkout latency",
                    "severity": "Sev0",
                    "monitorCondition": "Fired",
                    "targetResourceName": "checkout-app",
                    "startDateTime": "2026-08-20T10:00:00Z",
                },
                "context": {"condition": {"allOf": [{"metricValue": CANARY}]}},
            },
        },
        {
            "name": "alert-2",
            "properties": {
                "essentials": {
                    "alertRule": "queue depth",
                    "severity": "Sev3",
                    "monitorCondition": "Resolved",
                    "startDateTime": "2026-08-22T10:00:00Z",
                    "monitorConditionResolvedDateTime": "2026-08-22T11:00:00Z",
                },
                "context": {"description": CANARY},
            },
        },
    ]
}


def _azure_ready(monkeypatch):
    monkeypatch.setattr("yeaboi.connectors.azure_cloud.access_token", lambda: "tok")
    monkeypatch.setattr("yeaboi.connectors.fetching.read_json", lambda *a, **kw: AZURE_ALERTS)
    monkeypatch.setenv("AZURE_CLOUD_SUBSCRIPTION_ID", "sub")


class TestAzureFetch:
    def test_sev0_is_the_worst_severity_there(self, monkeypatch):
        # Azure numbers from zero; everyone else numbers from one.
        _azure_ready(monkeypatch)
        events = azure_cloud.fetch(START, END)
        assert events[0].severity == "critical"
        assert events[1].severity == "medium"

    def test_a_resolved_alert_says_so_and_when(self, monkeypatch):
        _azure_ready(monkeypatch)
        events = azure_cloud.fetch(START, END)
        assert events[0].status == "fired" and not events[0].ended_at
        assert events[1].status == "resolved" and events[1].ended_at

    def test_the_window_travels_on_the_request(self, monkeypatch):
        # Reporting reads a finished sprint, so this cannot be a lookback.
        url = azure_cloud.alerts_url("sub", START, END)
        assert "customTimeRange=2026-08-17T00:00:00Z/2026-08-31T00:00:00Z" in url

    def test_a_subscription_id_cannot_escape_its_path_segment(self):
        assert "/subscriptions/..%2Fevil/" in azure_cloud.alerts_url("../evil", START, END)


# --- the boundary ------------------------------------------------------------


class TestNoBodyCrossesTheBoundary:
    """Each fixture plants the canary in the field that vendor really returns."""

    @pytest.mark.parametrize("key", ["aws", "gcp", "azure_cloud"])
    def test_no_field_of_any_event_carries_it(self, monkeypatch, key):
        import dataclasses

        if key == "aws":
            _aws_ready(monkeypatch)
            events = aws.fetch(START, END)
        elif key == "gcp":
            monkeypatch.setattr("yeaboi.connectors.gcp.installed", lambda: True)
            monkeypatch.setattr("yeaboi.connectors.gcp.access_token", lambda: "tok")
            monkeypatch.setattr("yeaboi.connectors.fetching.read_json", lambda *a, **kw: GCP_GROUPS)
            monkeypatch.setenv("GCP_PROJECT_ID", "proj")
            events = gcp.fetch(START, END)
        else:
            _azure_ready(monkeypatch)
            events = azure_cloud.fetch(START, END)

        assert events, f"{key} returned nothing — the canary check would be vacuous"
        for event in events:
            for field in dataclasses.fields(event):
                assert CANARY not in str(getattr(event, field.name)), f"{key}.{field.name} carried the body"

    @pytest.mark.parametrize("key", ["aws", "gcp", "azure_cloud"])
    def test_no_event_names_a_person(self, monkeypatch, key):
        import dataclasses

        from yeaboi.ops.events import OpsEvent

        names = {f.name for f in dataclasses.fields(OpsEvent)}
        assert not names & {"author", "user", "owner", "assignee", "on_call"}
