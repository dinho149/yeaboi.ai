"""Every vendor's fetcher: what it asks for, and what it refuses to bring back.

No cassettes, for the reason the plan records: VCR here matches loosely, and the
assertion that matters is "we sent *this* URL with *these* headers". Each vendor
gets a recorded body shaped like the real one and a capturing fake.

The load-bearing test in this file is the last class: for every fetcher, no
event field carries anything the recorded body's own free-text fields held.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from yeaboi.connectors import datadog, grafana, incidentio, jsm_ops, launchdarkly, pagerduty, sentry, statuspage
from yeaboi.connectors.fetching import FetchError
from yeaboi.ops.events import OpsEvent

START = datetime(2026, 6, 1, tzinfo=timezone.utc)
END = datetime(2026, 6, 15, tzinfo=timezone.utc)

# A field on each vendor's payload that holds free text — a note, a summary, an
# exception message. None of it may reach an OpsEvent.
LEAK_CANARY = "PASSWORD=hunter2 at /srv/app/handlers.py line 88"


class Capture:
    """A stand-in for ``httpx.get`` that records the request and replays a body."""

    def __init__(self, payload, status: int = 200):
        self.payload, self.status = payload, status
        self.url, self.headers = "", {}

    def __call__(self, url, headers=None, timeout=None):
        self.url, self.headers = url, headers or {}
        return SimpleNamespace(
            status_code=self.status,
            json=lambda: self.payload,
            content=b"{}",
        )


def install(monkeypatch, payload, status: int = 200) -> Capture:
    capture = Capture(payload, status)
    monkeypatch.setattr("httpx.get", capture)
    # Every fetcher's host is public; the resolution guard is tested on its own.
    monkeypatch.setattr("yeaboi.connectors.http.assert_safe_url", lambda url: url)
    return capture


DATADOG_BODY = {
    "events": [
        {
            "id": 9001,
            "title": "[Triggered] checkout latency",
            "text": LEAK_CANARY,
            "alert_type": "error",
            "date_happened": int(datetime(2026, 6, 10, tzinfo=timezone.utc).timestamp()),
            "tags": ["env:prod", "service:checkout", "team:payments"],
            "url": "/event/event?id=9001",
        }
    ]
}

GRAFANA_BODY = {
    "data": {
        "groups": [
            {
                "file": "payments",
                "rules": [
                    {
                        "name": "checkout 5xx",
                        "state": "firing",
                        "activeAt": "2026-06-10T09:00:00Z",
                        "labels": {"severity": "critical", "service": "checkout"},
                        "annotations": {"description": LEAK_CANARY},
                    },
                    {"name": "quiet rule", "state": "inactive", "labels": {}},
                    {
                        "name": "old rule",
                        "state": "firing",
                        "activeAt": "2020-01-01T00:00:00Z",
                        "labels": {},
                    },
                ],
            }
        ]
    }
}

PAGERDUTY_BODY = {
    "incidents": [
        {
            "id": "PABCDEF",
            "incident_number": 4821,
            "title": "checkout is down",
            "description": LEAK_CANARY,
            "status": "resolved",
            "urgency": "high",
            "created_at": "2026-06-10T09:00:00Z",
            "resolved_at": "2026-06-10T10:30:00Z",
            "html_url": "https://acme.pagerduty.com/incidents/PABCDEF",
            "service": {"summary": "Checkout API"},
            "assignments": [{"assignee": {"summary": "A Person"}}],
        }
    ]
}

INCIDENTIO_BODY = {
    "incidents": [
        {
            "id": "01ABC",
            "reference": "INC-42",
            "name": "Payments degraded",
            "summary": LEAK_CANARY,
            "severity": {"name": "Major"},
            "incident_status": {"category": "closed"},
            "created_at": "2026-06-10T09:00:00Z",
            "permalink": "https://acme.incident.io/incidents/42",
        },
        {
            "id": "01OLD",
            "reference": "INC-1",
            "name": "Ancient history",
            "created_at": "2020-01-01T00:00:00Z",
            "incident_status": {"category": "closed"},
        },
    ]
}

SENTRY_BODY = [
    {
        "id": "77",
        "shortId": "WEB-3F",
        "title": f"ValueError: {LEAK_CANARY}",
        "culprit": "checkout.views in submit",
        "metadata": {"type": "ValueError", "value": LEAK_CANARY},
        "level": "error",
        "status": "unresolved",
        "firstSeen": "2026-06-10T09:00:00Z",
        "permalink": "https://sentry.io/organizations/acme/issues/77/",
        "project": {"slug": "web"},
    }
]


JSM_OPS_BODY = {
    "values": [
        {
            "id": "a-1",
            "tinyId": "42",
            "message": "Checkout is down",
            "note": LEAK_CANARY,
            "status": "closed",
            "priority": "P2",
            "entity": "checkout",
            "createdAt": "2026-06-10T09:00:00Z",
            "updatedAt": "2026-06-10T10:30:00Z",
        },
        {
            "id": "a-0",
            "tinyId": "1",
            "message": "Ancient history",
            "status": "closed",
            "priority": "P5",
            "createdAt": "2020-01-01T00:00:00Z",
        },
    ]
}

LAUNCHDARKLY_BODY = {
    "items": [
        {
            "_id": "ld-1",
            "kind": "flag",
            "name": "checkout-v2",
            "title": f"A Person turned on the flag {LEAK_CANARY}",
            "titleVerb": "turned on the flag",
            "description": LEAK_CANARY,
            "date": 1781082000000,  # 2026-06-10T09:00:00Z
            "_links": {"site": {"href": "/acme/production/features/checkout-v2"}},
        },
        {"_id": "ld-2", "kind": "member", "name": "A Person", "date": 1781082000000},
    ]
}

STATUSPAGE_BODY = [
    {
        "id": "inc-9",
        "name": "Checkout degraded",
        "impact": "major",
        "status": "resolved",
        "started_at": "2026-06-10T09:00:00Z",
        "resolved_at": "2026-06-10T10:30:00Z",
        "shortlink": "https://stspg.io/abc",
        "components": [{"name": "Checkout API"}],
        "incident_updates": [{"body": LEAK_CANARY}],
    },
    {
        "id": "inc-1",
        "name": "Ancient history",
        "impact": "minor",
        "status": "resolved",
        "started_at": "2020-01-01T00:00:00Z",
    },
]


@pytest.fixture
def connected(monkeypatch):
    for env, value in {
        "DATADOG_API_KEY": "dd-api",
        "DATADOG_APP_KEY": "dd-app",
        "DATADOG_SITE": "datadoghq.eu",
        "GRAFANA_BASE_URL": "https://grafana.example.com",
        "GRAFANA_API_TOKEN": "graf-tok",
        "PAGERDUTY_API_KEY": "pd-tok",
        "INCIDENTIO_API_KEY": "inc-tok",
        "SENTRY_AUTH_TOKEN": "sentry-tok",
        "SENTRY_ORG": "acme",
        "SENTRY_BASE_URL": "",
        "STATUSPAGE_API_KEY": "sp-tok",
        "STATUSPAGE_PAGE_ID": "abc123",
        "LAUNCHDARKLY_API_KEY": "ld-tok",
        "JSM_OPS_API_KEY": "jsm-tok",
        "JSM_OPS_CLOUD_ID": "cloud-1",
    }.items():
        monkeypatch.setenv(env, value)


class TestDatadog:
    def test_asks_the_events_api_for_alerts_only(self, monkeypatch, connected):
        capture = install(monkeypatch, DATADOG_BODY)
        datadog.fetch(START, END)
        assert capture.url.startswith("https://api.datadoghq.eu/api/v1/events?")
        assert "sources=alert" in capture.url
        assert f"start={int(START.timestamp())}" in capture.url
        assert capture.headers == {"DD-API-KEY": "dd-api", "DD-APPLICATION-KEY": "dd-app"}

    def test_reads_the_service_tag_and_absolutises_the_url(self, monkeypatch, connected):
        install(monkeypatch, DATADOG_BODY)
        (found,) = datadog.fetch(START, END)
        assert (found.kind, found.source, found.service) == ("alert", "datadog", "checkout")
        assert found.severity == "high"
        assert found.url == "https://app.datadoghq.eu/event/event?id=9001"

    def test_a_recovery_reads_as_resolved(self, monkeypatch, connected):
        install(monkeypatch, {"events": [{**DATADOG_BODY["events"][0], "alert_type": "success"}]})
        assert datadog.fetch(START, END)[0].resolved

    def test_an_unknown_site_falls_back_rather_than_being_trusted(self, monkeypatch, connected):
        monkeypatch.setenv("DATADOG_SITE", "evil.example.com")
        capture = install(monkeypatch, DATADOG_BODY)
        datadog.fetch(START, END)
        assert capture.url.startswith("https://api.datadoghq.com/")


class TestGrafana:
    def test_asks_the_viewer_readable_rules_endpoint(self, monkeypatch, connected):
        capture = install(monkeypatch, GRAFANA_BODY)
        grafana.fetch(START, END)
        assert capture.url == "https://grafana.example.com/api/prometheus/grafana/api/v1/rules"
        assert capture.headers["Authorization"] == "Bearer graf-tok"

    def test_keeps_only_firing_rules_inside_the_window(self, monkeypatch, connected):
        install(monkeypatch, GRAFANA_BODY)
        found = grafana.fetch(START, END)
        assert [e.title for e in found] == ["checkout 5xx"]
        assert (found[0].severity, found[0].service) == ("critical", "checkout")

    def test_falls_back_to_the_namespace_when_no_service_label(self, monkeypatch, connected):
        body = {
            "data": {
                "groups": [
                    {
                        "file": "payments",
                        "rules": [{"name": "r", "state": "firing", "activeAt": "2026-06-10T09:00:00Z", "labels": {}}],
                    }
                ]
            }
        }
        install(monkeypatch, body)
        assert grafana.fetch(START, END)[0].service == "payments"

    def test_a_changed_shape_yields_nothing_rather_than_raising(self, monkeypatch, connected):
        install(monkeypatch, {"data": {"groups": "not a list"}})
        assert grafana.fetch(START, END) == ()


class TestPagerDuty:
    def test_sends_the_window_and_the_versioned_accept_header(self, monkeypatch, connected):
        capture = install(monkeypatch, PAGERDUTY_BODY)
        pagerduty.fetch(START, END)
        assert capture.url.startswith("https://api.pagerduty.com/incidents?")
        assert "since=2026-06-01T00:00:00Z" in capture.url
        assert "until=2026-06-15T00:00:00Z" in capture.url
        assert capture.headers["Authorization"] == "Token token=pd-tok"
        assert capture.headers["Accept"] == "application/vnd.pagerduty+json;version=2"

    def test_uses_the_human_incident_number_as_the_ref(self, monkeypatch, connected):
        install(monkeypatch, PAGERDUTY_BODY)
        (found,) = pagerduty.fetch(START, END)
        assert found.ref == "PD-4821"
        assert (found.kind, found.service, found.severity) == ("incident", "Checkout API", "high")
        assert found.ended_at == "2026-06-10T10:30:00Z"

    def test_the_assignee_never_reaches_an_event(self, monkeypatch, connected):
        # The rota records the schedule, not the engineer — and there is nowhere
        # on an OpsEvent to put one even if a fetcher tried.
        install(monkeypatch, PAGERDUTY_BODY)
        (found,) = pagerduty.fetch(START, END)
        assert "A Person" not in repr(found)


class TestIncidentIO:
    def test_asks_v2_incidents_with_a_bearer_token(self, monkeypatch, connected):
        capture = install(monkeypatch, INCIDENTIO_BODY)
        incidentio.fetch(START, END)
        assert capture.url.startswith("https://api.incident.io/v2/incidents?")
        assert capture.headers["Authorization"] == "Bearer inc-tok"

    def test_filters_the_window_locally(self, monkeypatch, connected):
        install(monkeypatch, INCIDENTIO_BODY)
        found = incidentio.fetch(START, END)
        assert [e.ref for e in found] == ["INC-42"]
        assert (found[0].severity, found[0].status) == ("high", "resolved")

    def test_an_open_category_is_carried_through(self, monkeypatch, connected):
        body = {"incidents": [{**INCIDENTIO_BODY["incidents"][0], "incident_status": {"category": "active"}}]}
        install(monkeypatch, body)
        found = incidentio.fetch(START, END)
        assert (found[0].status, found[0].resolved) == ("active", False)


class TestSentry:
    def test_quotes_the_org_slug_into_the_path(self, monkeypatch, connected):
        monkeypatch.setenv("SENTRY_ORG", "acme/../evil")
        capture = install(monkeypatch, SENTRY_BODY)
        sentry.fetch(START, END)
        # The slug is user-supplied: a slash in it must not become a path segment.
        assert "/organizations/acme%2F..%2Fevil/issues/" in capture.url

    def test_sends_the_window_not_a_stats_period(self, monkeypatch, connected):
        capture = install(monkeypatch, SENTRY_BODY)
        sentry.fetch(START, END)
        assert "statsPeriod=&" in capture.url or capture.url.endswith("statsPeriod=&limit=100")
        assert "utc=true" in capture.url
        assert capture.headers["Authorization"] == "Bearer sentry-tok"

    def test_names_an_issue_by_type_and_culprit_never_by_its_message(self, monkeypatch, connected):
        # Sentry's title is the exception class AND its message, and that message
        # routinely quotes the input that caused it.
        install(monkeypatch, SENTRY_BODY)
        (found,) = sentry.fetch(START, END)
        assert found.title == "ValueError in checkout.views in submit"
        assert LEAK_CANARY not in found.title

    def test_falls_back_to_the_title_only_when_nothing_safer_exists(self, monkeypatch, connected):
        install(monkeypatch, [{"id": "1", "title": "Something broke"}])
        assert sentry.fetch(START, END)[0].title == "Something broke"


class TestStatuspage:
    def test_it_scopes_the_read_to_the_page_with_the_oauth_scheme(self, monkeypatch, connected):
        capture = install(monkeypatch, STATUSPAGE_BODY)
        statuspage.fetch(START, END)
        assert capture.url == "https://api.statuspage.io/v1/pages/abc123/incidents?limit=100"
        assert capture.headers["Authorization"] == "OAuth sp-tok"

    def test_a_page_id_cannot_escape_its_path_segment(self, monkeypatch, connected):
        monkeypatch.setenv("STATUSPAGE_PAGE_ID", "abc/../evil")
        capture = install(monkeypatch, [])
        statuspage.fetch(START, END)
        assert "/pages/abc%2F..%2Fevil/incidents" in capture.url

    def test_grades_impact_and_stops_at_the_window_edge(self, monkeypatch, connected):
        install(monkeypatch, STATUSPAGE_BODY)
        found = statuspage.fetch(START, END)
        # Rows come newest first, so the 2020 incident ends the walk.
        assert [e.ref for e in found] == ["inc-9"]
        assert (found[0].kind, found[0].severity, found[0].status) == ("incident", "high", "resolved")
        assert found[0].service == "Checkout API"
        assert found[0].url == "https://stspg.io/abc"

    def test_a_changed_shape_yields_nothing_rather_than_raising(self, monkeypatch, connected):
        install(monkeypatch, {"incidents": "not a list"})
        assert statuspage.fetch(START, END) == ()


class TestLaunchDarkly:
    def test_the_window_rides_the_request_in_epoch_ms(self, monkeypatch, connected):
        capture = install(monkeypatch, LAUNCHDARKLY_BODY)
        launchdarkly.fetch(START, END)
        assert capture.url.startswith("https://app.launchdarkly.com/api/v2/auditlog?")
        assert "after=1780272000000" in capture.url and "before=1781481600000" in capture.url
        assert capture.headers["Authorization"] == "ld-tok"

    def test_only_flag_entries_become_events(self, monkeypatch, connected):
        install(monkeypatch, LAUNCHDARKLY_BODY)
        found = launchdarkly.fetch(START, END)
        # The member entry is dropped unread — no person reaches an event.
        assert [e.ref for e in found] == ["ld-1"]
        assert (found[0].kind, found[0].severity) == ("deploy", "info")
        assert found[0].title == "Flag changed: checkout-v2"
        assert found[0].url == "https://app.launchdarkly.com/acme/production/features/checkout-v2"
        assert "A Person" not in repr(found)

    def test_a_changed_shape_yields_nothing_rather_than_raising(self, monkeypatch, connected):
        install(monkeypatch, {"items": "not a list"})
        assert launchdarkly.fetch(START, END) == ()


class TestJsmOps:
    def test_it_asks_newest_first_with_the_geniekey_scheme(self, monkeypatch, connected):
        capture = install(monkeypatch, JSM_OPS_BODY)
        jsm_ops.fetch(START, END)
        assert capture.url == (
            "https://api.atlassian.com/jsm/ops/api/cloud-1/v1/alerts?limit=100&sort=createdAt&order=desc"
        )
        assert capture.headers["Authorization"] == "GenieKey jsm-tok"

    def test_grades_priority_and_stops_at_the_window_edge(self, monkeypatch, connected):
        install(monkeypatch, JSM_OPS_BODY)
        found = jsm_ops.fetch(START, END)
        # Rows come newest first, so the 2020 alert ends the walk.
        assert [e.ref for e in found] == ["42"]
        assert (found[0].kind, found[0].severity, found[0].status) == ("alert", "high", "closed")
        assert found[0].service == "checkout"
        assert found[0].ended_at == "2026-06-10T10:30:00Z"

    def test_a_cloud_id_cannot_escape_its_path_segment(self, monkeypatch, connected):
        monkeypatch.setenv("JSM_OPS_CLOUD_ID", "cloud/../evil")
        capture = install(monkeypatch, {"values": []})
        jsm_ops.fetch(START, END)
        assert "/jsm/ops/api/cloud%2F..%2Fevil/v1/alerts" in capture.url

    def test_a_changed_shape_yields_nothing_rather_than_raising(self, monkeypatch, connected):
        install(monkeypatch, {"values": "not a list"})
        assert jsm_ops.fetch(START, END) == ()


class TestFailures:
    """A vendor saying no must be a message, never a traceback or a credential."""

    def test_a_rejected_credential_names_the_fix(self, monkeypatch, connected):
        install(monkeypatch, {}, status=401)
        with pytest.raises(FetchError, match="verify pagerduty"):
            pagerduty.fetch(START, END)

    def test_rate_limiting_suggests_a_shorter_window(self, monkeypatch, connected):
        install(monkeypatch, {}, status=429)
        with pytest.raises(FetchError, match="shorter window"):
            pagerduty.fetch(START, END)

    def test_a_non_json_body_is_a_message(self, monkeypatch, connected):
        def boom(url, headers=None, timeout=None):
            def raiser():
                raise ValueError("not json")

            return SimpleNamespace(status_code=200, json=raiser, content=b"<html>")

        monkeypatch.setattr("httpx.get", boom)
        monkeypatch.setattr("yeaboi.connectors.http.assert_safe_url", lambda url: url)
        with pytest.raises(FetchError, match="not JSON"):
            pagerduty.fetch(START, END)

    def test_no_credential_reaches_a_failure_message(self, monkeypatch, connected):
        install(monkeypatch, {}, status=500)
        with pytest.raises(FetchError) as excinfo:
            pagerduty.fetch(START, END)
        assert "pd-tok" not in str(excinfo.value)

    def test_an_unsafe_url_never_leaves(self, monkeypatch, connected):
        monkeypatch.setenv("GRAFANA_BASE_URL", "http://169.254.169.254")
        monkeypatch.setattr("httpx.get", lambda *a, **k: pytest.fail("a request left for the metadata host"))
        with pytest.raises(FetchError):
            grafana.fetch(START, END)


class TestNoBodyCrossesTheBoundary:
    """The guarantee, asserted on every fetcher's actual output.

    Each recorded body plants the same canary in a free-text field the vendor
    really returns — Datadog's ``text``, Grafana's annotation, PagerDuty's
    ``description``, incident.io's ``summary``, Sentry's exception message. None
    of it may appear anywhere in the events that come back.
    """

    CASES = [
        (datadog, DATADOG_BODY),
        (grafana, GRAFANA_BODY),
        (pagerduty, PAGERDUTY_BODY),
        (incidentio, INCIDENTIO_BODY),
        (sentry, SENTRY_BODY),
        (statuspage, STATUSPAGE_BODY),
        (launchdarkly, LAUNCHDARKLY_BODY),
        (jsm_ops, JSM_OPS_BODY),
    ]

    @pytest.mark.parametrize(("module", "body"), CASES, ids=lambda v: getattr(v, "__name__", ""))
    def test_the_canary_never_comes_back(self, monkeypatch, connected, module, body):
        install(monkeypatch, body)
        found = module.fetch(START, END)
        assert found, f"{module.__name__} returned nothing — the guard would pass vacuously"
        for event in found:
            assert LEAK_CANARY not in repr(event)

    @pytest.mark.parametrize(("module", "body"), CASES, ids=lambda v: getattr(v, "__name__", ""))
    def test_every_fetcher_returns_ops_events_of_a_known_kind(self, monkeypatch, connected, module, body):
        from yeaboi.ops.events import EVENT_KINDS

        install(monkeypatch, body)
        for event in module.fetch(START, END):
            assert isinstance(event, OpsEvent)
            assert event.kind in EVENT_KINDS
            assert event.source == module.CONNECTOR.key
