"""PagerDuty — incidents and who the schedule was pointing at.

One credential, one host: the simplest descriptor in the set, and deliberately
so. What it reads is bounded to incident counts and titles; the on-call rota is
read to scope a query, never to name a person in an artifact.
"""

from __future__ import annotations

from datetime import datetime

from yeaboi.connectors.spec import Connector, ConnectorField
from yeaboi.ops.events import OpsEvent, clean_severity, clean_title, iso, parse_ts

CONNECTOR = Connector(
    key="pagerduty",
    label="PagerDuty",
    family="incidents",
    section="connections",
    summary="Incidents raised during the sprint, and how long they ran",
    detail=(
        "yeaboi reads incident counts, titles and durations over the window a mode "
        "already covers, so planning can see the week a team spent firefighting "
        "instead of guessing why velocity moved. It never reads notes or "
        "postmortems, never acknowledges or resolves anything, and never puts an "
        "incident against a person — the rota records the schedule, not the engineer."
    ),
    verify="_verify_pagerduty",
    fetch="fetch",
    docs_url="https://support.pagerduty.com/main/docs/api-access-keys",
    accent="rgb(6,172,105)",
    fields=(
        ConnectorField(
            env="PAGERDUTY_API_KEY",
            label="API Key",
            secret=True,
            verify_arg="token",
            help_url="https://support.pagerduty.com/main/docs/api-access-keys",
            help_scope="A General Access REST API key, read-only — yeaboi never writes",
        ),
    ),
)


API_BASE = "https://api.pagerduty.com"


def fetch(window_start: datetime, window_end: datetime) -> tuple[OpsEvent, ...]:
    """Incidents PagerDuty raised in the window.

    ``since``/``until`` are the vendor's own window parameters, so the filtering
    happens at their end. Notes, log entries and the on-call rota are not
    requested at all: the rota measures the schedule, not the engineer, and this
    layer has no field to put a person in.
    """
    from yeaboi.connectors.fetching import PAGE_LIMIT, env, read_json, rows

    token = env("PAGERDUTY_API_KEY")
    url = (
        f"{API_BASE}/incidents"
        f"?since={iso(window_start)}&until={iso(window_end)}"
        f"&limit={PAGE_LIMIT}&sort_by=created_at:desc"
    )
    body = read_json(
        url,
        headers={
            "Authorization": f"Token token={token}",
            "Accept": "application/vnd.pagerduty+json;version=2",
        },
        source="pagerduty",
    )

    events = []
    for row in rows(body, "incidents"):
        service = row.get("service") if isinstance(row.get("service"), dict) else {}
        status = str(row.get("status") or "")
        events.append(
            OpsEvent(
                kind="incident",
                source="pagerduty",
                # The human-facing number, not the opaque id: it is what a
                # person sees in PagerDuty and what a provenance input records.
                ref=f"PD-{row.get('incident_number')}" if row.get("incident_number") else str(row.get("id") or ""),
                title=clean_title(str(row.get("title") or "")),
                service=str(service.get("summary") or ""),
                severity=clean_severity(str(row.get("urgency") or "")),
                status=status,
                started_at=iso(parse_ts(str(row.get("created_at") or ""))),
                ended_at=iso(parse_ts(str(row.get("resolved_at") or ""))),
                url=str(row.get("html_url") or ""),
            )
        )
    return tuple(events)
