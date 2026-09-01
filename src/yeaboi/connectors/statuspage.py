"""Statuspage — the incidents a team already tells its customers about.

One API key scoped to one page. The page ID is an identifier riding the URL
path, so it is quoted wherever it lands; the host is fixed.
"""

from __future__ import annotations

from datetime import datetime
from urllib.parse import quote

from yeaboi.connectors.spec import Connector, ConnectorField
from yeaboi.ops.events import OpsEvent, clean_title, iso, parse_ts

API_BASE = "https://api.statuspage.io/v1"

#: A Statuspage impact, and what it is worth to a sprint. Maintenance is real
#: work but not a fire, so it lands as info rather than a severity.
_IMPACTS: dict[str, str] = {
    "critical": "critical",
    "major": "high",
    "minor": "medium",
    "none": "info",
    "maintenance": "info",
}


CONNECTOR = Connector(
    key="statuspage",
    label="Statuspage",
    family="incidents",
    section="connections",
    summary="Incidents from your status page, so what customers saw reaches planning",
    detail=(
        "yeaboi reads incident names, impacts and timings from one status "
        "page over the window a mode already covers. It never reads incident "
        "updates or subscriber data, and it never creates, updates or "
        "resolves an incident."
    ),
    verify="_verify_statuspage",
    fetch="fetch",
    docs_url="https://support.atlassian.com/statuspage/docs/create-and-manage-api-keys/",
    glyph="\U0001f7e2",  # 🟢 — the status dot
    accent="rgb(23,43,77)",
    fields=(
        ConnectorField(
            env="STATUSPAGE_API_KEY",
            label="API Key",
            secret=True,
            verify_arg="token",
            help_url="https://manage.statuspage.io",
            help_scope="A user API key from your Statuspage account — yeaboi only ever reads",
        ),
        ConnectorField(
            env="STATUSPAGE_PAGE_ID",
            label="Page ID",
            verify_arg="page_id",
            placeholder="abc123def456",
            hint="The page's ID from its API settings, not its display name",
        ),
    ),
)


def fetch(window_start: datetime, window_end: datetime) -> tuple[OpsEvent, ...]:
    """Incidents on the page in the window, one event per incident.

    Rows come newest first, so the walk stops at the window edge. Incident
    updates — the prose a team writes mid-incident — are never read.
    """
    from yeaboi.connectors.fetching import PAGE_LIMIT, env, read_json, rows

    page = quote(env("STATUSPAGE_PAGE_ID").strip().strip("/"), safe="")
    url = f"{API_BASE}/pages/{page}/incidents?limit={PAGE_LIMIT}"
    body = read_json(url, headers={"Authorization": f"OAuth {env('STATUSPAGE_API_KEY')}"}, source="statuspage")

    events: list[OpsEvent] = []
    for row in rows(body, ""):
        started = parse_ts(str(row.get("started_at") or row.get("created_at") or ""))
        if started and started < window_start:
            break  # newest first — everything after this is older still
        components = row.get("components") if isinstance(row.get("components"), list) else []
        first = components[0] if components and isinstance(components[0], dict) else {}
        events.append(
            OpsEvent(
                kind="incident",
                source="statuspage",
                ref=str(row.get("id") or ""),
                title=clean_title(str(row.get("name") or "")),
                service=str(first.get("name") or ""),
                severity=_IMPACTS.get(str(row.get("impact") or ""), "info"),
                status=str(row.get("status") or ""),
                started_at=iso(started),
                ended_at=iso(parse_ts(str(row.get("resolved_at") or ""))),
                url=str(row.get("shortlink") or ""),
            )
        )
        if len(events) >= PAGE_LIMIT:
            break
    return tuple(events)
