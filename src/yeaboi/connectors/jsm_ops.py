"""Jira Service Management Ops — alerts, through Opsgenie's successor.

Its own API key, never Jira's: JSM Ops keys use the GenieKey scheme against
``api.atlassian.com``, and pairing the probe with a Jira credential would only
teach users to paste the wrong secret. The cloud ID is an identifier riding
the URL path, so it is quoted wherever it lands; the host is fixed.
"""

from __future__ import annotations

from datetime import datetime
from urllib.parse import quote

from yeaboi.connectors.spec import Connector, ConnectorField
from yeaboi.ops.events import OpsEvent, clean_title, iso, parse_ts

API_BASE = "https://api.atlassian.com/jsm/ops/api"

#: A JSM Ops priority, and what it is worth to a sprint.
_PRIORITIES: dict[str, str] = {
    "P1": "critical",
    "P2": "high",
    "P3": "medium",
    "P4": "low",
    "P5": "info",
}


def alerts_base(cloud_id: str) -> str:
    """The alerts API root for one site, with the cloud ID quoted into place."""
    return f"{API_BASE}/{quote(cloud_id.strip().strip('/'), safe='')}/v1"


CONNECTOR = Connector(
    key="jsm_ops",
    label="JSM Ops alerts",
    family="incidents",
    section="connections",
    summary="Alerts raised during the sprint, from Jira Service Management Ops",
    detail=(
        "yeaboi reads alert counts, priorities and timings over the window a "
        "mode already covers — Opsgenie's successor inside Jira Service "
        "Management. It never reads alert notes or responder identities, "
        "never acknowledges or closes anything, and never puts an alert "
        "against a person."
    ),
    verify="_verify_jsm_ops",
    fetch="fetch",
    docs_url="https://support.atlassian.com/jira-service-management-cloud/docs/create-an-api-key-for-operations/",
    glyph="\U0001f9de",  # 🧞 — the (Ops)genie
    accent="rgb(101,84,192)",
    fields=(
        ConnectorField(
            env="JSM_OPS_API_KEY",
            label="Ops API Key",
            secret=True,
            verify_arg="token",
            help_url="https://support.atlassian.com/jira-service-management-cloud/docs/create-an-api-key-for-operations/",
            help_scope="An Operations API key from JSM settings — its own key, never your Jira API token",
        ),
        ConnectorField(
            env="JSM_OPS_CLOUD_ID",
            label="Cloud ID",
            verify_arg="cloud_id",
            placeholder="00000000-0000-0000-0000-000000000000",
            hint="Your Atlassian site's cloud ID — from admin.atlassian.com or <site>.atlassian.net/_edge/tenant_info",
        ),
    ),
)


def fetch(window_start: datetime, window_end: datetime) -> tuple[OpsEvent, ...]:
    """Alerts raised in the window, one event per alert.

    Rows come newest first, so the walk stops at the window edge. Notes and
    responder identities are never requested — the alert contributes what
    fired and when, never who was woken.
    """
    from yeaboi.connectors.fetching import PAGE_LIMIT, env, read_json, rows

    url = f"{alerts_base(env('JSM_OPS_CLOUD_ID'))}/alerts?limit={PAGE_LIMIT}&sort=createdAt&order=desc"
    body = read_json(url, headers={"Authorization": f"GenieKey {env('JSM_OPS_API_KEY')}"}, source="jsm_ops")

    events: list[OpsEvent] = []
    for row in rows(body, "values"):
        started = parse_ts(str(row.get("createdAt") or ""))
        if started and started < window_start:
            break  # newest first — everything after this is older still
        status = str(row.get("status") or "")
        events.append(
            OpsEvent(
                kind="alert",
                source="jsm_ops",
                ref=str(row.get("tinyId") or row.get("id") or ""),
                title=clean_title(str(row.get("message") or "")),
                service=str(row.get("entity") or ""),
                severity=_PRIORITIES.get(str(row.get("priority") or ""), "info"),
                status=status,
                started_at=iso(started),
                ended_at=iso(parse_ts(str(row.get("updatedAt") or ""))) if status == "closed" else "",
                url="",
            )
        )
        if len(events) >= PAGE_LIMIT:
            break
    return tuple(events)
