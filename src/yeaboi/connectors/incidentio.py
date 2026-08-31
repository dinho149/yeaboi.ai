"""incident.io — incidents, on one global host.

The simplest descriptor in the catalog: one bearer token, no host field and no
region, so there is nothing here for a caller to redirect. ``GET /v1/identity``
returns the key's roles as well as its name, which is what lets verification
report the scope a key actually carries instead of taking read-only on trust.
"""

from __future__ import annotations

from datetime import datetime

from yeaboi.connectors.spec import Connector, ConnectorField
from yeaboi.ops.events import OpsEvent, clean_severity, clean_title, iso, parse_ts

API_BASE = "https://api.incident.io"

CONNECTOR = Connector(
    key="incidentio",
    label="incident.io",
    family="incidents",
    section="connections",
    summary="Incidents declared during the sprint, and how long they ran",
    detail=(
        "yeaboi reads incident counts, severities and durations over the window "
        "a mode already covers. It never reads the incident channel, the "
        "timeline or a postmortem, never declares, updates or closes an "
        "incident, and never attributes one to whoever was the lead."
    ),
    verify="_verify_incidentio",
    fetch="fetch",
    docs_url="https://docs.incident.io/api-reference",
    # The mark's own colour, read from the vendor's favicon.
    accent="rgb(242,85,51)",
    fields=(
        ConnectorField(
            env="INCIDENTIO_API_KEY",
            label="API Key",
            secret=True,
            verify_arg="token",
            help_url="https://app.incident.io/settings/api-keys",
            help_scope="A key with the view-only roles — yeaboi never writes",
        ),
    ),
)


#: Which of incident.io's status categories mean the incident is over.
_CLOSED = frozenset({"closed", "declined", "canceled", "cancelled"})


def fetch(window_start: datetime, window_end: datetime) -> tuple[OpsEvent, ...]:
    """Incidents declared in the window.

    The V2 incidents list, filtered on ``created_at`` here rather than on the
    wire: the endpoint's date filters vary by field configuration, and filtering
    one bounded page locally is both simpler and impossible to get subtly wrong.
    ``summary`` — the human write-up — is returned by the API and never read.
    """
    from yeaboi.connectors.fetching import PAGE_LIMIT, env, read_json, rows

    token = env("INCIDENTIO_API_KEY")
    body = read_json(
        f"{API_BASE}/v2/incidents?page_size={PAGE_LIMIT}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        source="incidentio",
    )

    events = []
    for row in rows(body, "incidents"):
        started = parse_ts(str(row.get("created_at") or ""))
        if started is not None and not (window_start <= started <= window_end):
            continue
        severity = row.get("severity") if isinstance(row.get("severity"), dict) else {}
        status = row.get("incident_status") if isinstance(row.get("incident_status"), dict) else {}
        category = str(status.get("category") or "").lower()
        events.append(
            OpsEvent(
                kind="incident",
                source="incidentio",
                ref=str(row.get("reference") or row.get("id") or ""),
                title=clean_title(str(row.get("name") or "")),
                severity=clean_severity(str(severity.get("name") or "")),
                status="resolved" if category in _CLOSED else category or "open",
                started_at=iso(started),
                url=str(row.get("permalink") or ""),
            )
        )
    return tuple(events)
