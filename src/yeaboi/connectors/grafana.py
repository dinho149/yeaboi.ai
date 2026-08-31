"""Grafana — the connector whose host belongs to the user.

Datadog picks a host from a closed list; Grafana is wherever the team runs it,
so the base URL is typed. That makes it the descriptor's real test: every
request goes through ``connectors.http.assert_safe_url``, which is what keeps a
typed URL from reaching the loopback interface or a cloud metadata endpoint.
Read-only — nothing here writes to Grafana.
"""

from __future__ import annotations

from datetime import datetime

from yeaboi.connectors.spec import Connector, ConnectorField
from yeaboi.ops.events import OpsEvent, clean_severity, clean_title, iso, parse_ts

CONNECTOR = Connector(
    key="grafana",
    label="Grafana",
    family="observability",
    section="connections",
    summary="Firing alert rules from your own Grafana, cloud or self-hosted",
    detail=(
        "yeaboi reads the alert rules that fired over the window a mode already "
        "covers and counts them by service. It never reads dashboards, panels or "
        "the data sources behind them, and it never edits a rule or silences an "
        "alert. Works against Grafana Cloud and a self-hosted instance alike — the "
        "URL you give is the one it talks to, and only over https."
    ),
    verify="_verify_grafana",
    fetch="fetch",
    docs_url="https://grafana.com/docs/grafana/latest/administration/service-accounts/",
    accent="rgb(242,131,32)",
    fields=(
        ConnectorField(
            env="GRAFANA_BASE_URL",
            label="Base URL",
            verify_arg="base_url",
            placeholder="https://yourteam.grafana.net",
            hint="Your Grafana root — https only",
        ),
        ConnectorField(
            env="GRAFANA_API_TOKEN",
            label="Service Account Token",
            secret=True,
            verify_arg="token",
            help_url="https://grafana.com/docs/grafana/latest/administration/service-accounts/",
            help_scope="A service account token with the Viewer role — yeaboi only ever reads",
        ),
    ),
)


def fetch(window_start: datetime, window_end: datetime) -> tuple[OpsEvent, ...]:
    """Alert rules Grafana says are firing, kept to those that started in the window.

    The Prometheus-compatible rules endpoint, because it is the one every
    unified-alerting Grafana serves to a Viewer — the state-history API needs a
    Loki backend most installs do not have. That makes this a snapshot of what
    is firing *now*, filtered by when it started, rather than a full history: a
    rule that fired and recovered inside the window is not visible, and the
    ``detail`` copy promises firing rules rather than every alert ever raised.
    """
    from yeaboi.connectors.fetching import PAGE_LIMIT, env, read_json

    base = env("GRAFANA_BASE_URL").rstrip("/")
    token = env("GRAFANA_API_TOKEN")
    body = read_json(
        f"{base}/api/prometheus/grafana/api/v1/rules",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        source="grafana",
    )

    groups = (body.get("data") or {}).get("groups") or [] if isinstance(body, dict) else []
    events = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        namespace = str(group.get("file") or "")
        for rule in group.get("rules") or []:
            if not isinstance(rule, dict) or str(rule.get("state") or "") != "firing":
                continue
            started = parse_ts(str(rule.get("activeAt") or "")) or _first_active(rule)
            if started is not None and not (window_start <= started <= window_end):
                continue
            labels = rule.get("labels") if isinstance(rule.get("labels"), dict) else {}
            events.append(
                OpsEvent(
                    kind="alert",
                    source="grafana",
                    ref=str(rule.get("name") or ""),
                    title=clean_title(str(rule.get("name") or "")),
                    service=str(labels.get("service") or namespace),
                    severity=clean_severity(str(labels.get("severity") or "")),
                    status="firing",
                    started_at=iso(started),
                    url=f"{base}/alerting/list",
                )
            )
            if len(events) >= PAGE_LIMIT:
                return tuple(events)
    return tuple(events)


def _first_active(rule: dict) -> datetime | None:
    """When the earliest of a rule's alert instances started, if it says."""
    stamps = [
        parse_ts(str(alert.get("activeAt") or "")) for alert in rule.get("alerts") or [] if isinstance(alert, dict)
    ]
    found = [s for s in stamps if s is not None]
    return min(found) if found else None
