"""Datadog — the first ops connector.

Two credentials rather than one (the API key authenticates, the application key
authorises), and a site that *derives* the base URL instead of being typed:
Datadog runs the same API on five regional hosts and a wrong one fails as an
auth error. Read-only — nothing here writes to Datadog.
"""

from __future__ import annotations

from datetime import datetime

from yeaboi.connectors.spec import Connector, ConnectorField
from yeaboi.ops.events import OpsEvent, clean_severity, clean_title, iso, parse_ts

#: Datadog's regional hosts. The API path is identical on each; only the host
#: differs, so the base URL is derived rather than asked for.
SITES: tuple[str, ...] = (
    "datadoghq.com",
    "datadoghq.eu",
    "us3.datadoghq.com",
    "us5.datadoghq.com",
    "ddog-gov.com",
)
DEFAULT_SITE = "datadoghq.com"


def api_base(site: str) -> str:
    """The API root for a site, falling back to the default for an unknown one."""
    chosen = (site or "").strip() or DEFAULT_SITE
    if chosen not in SITES:
        chosen = DEFAULT_SITE
    return f"https://api.{chosen}"


CONNECTOR = Connector(
    key="datadog",
    label="Datadog",
    family="observability",
    section="connections",
    summary="Monitors and alerts — what production did while the sprint ran",
    detail=(
        "yeaboi reads triggered monitors and alert events over the window a mode "
        "already covers, and turns them into counts and a conflict card when the "
        "board says a ticket is done and production says otherwise. It never reads "
        "logs, metric series or dashboards, and never attributes an alert to a person."
    ),
    verify="_verify_datadog",
    fetch="fetch",
    docs_url="https://docs.datadoghq.com/account_management/api-app-keys/",
    accent="rgb(147,111,218)",
    fields=(
        ConnectorField(
            env="DATADOG_API_KEY",
            label="API Key",
            secret=True,
            verify_arg="token",
            help_url="https://app.datadoghq.com/organization-settings/api-keys",
            help_scope="Organization Settings → API Keys — a plain API key, no scopes to pick",
        ),
        ConnectorField(
            env="DATADOG_APP_KEY",
            label="Application Key",
            secret=True,
            verify_arg="app_key",
            help_url="https://app.datadoghq.com/organization-settings/application-keys",
            help_scope="Needs monitors_read and events_read — yeaboi only ever reads",
        ),
        ConnectorField(
            env="DATADOG_SITE",
            label="Site",
            required=False,
            choices=SITES,
            default=DEFAULT_SITE,
            # Deliberately NOT a verify_arg: a caller-supplied site paired with
            # the stored token would send that token to a host of the caller's
            # choosing — the same exfiltration verify_connection's base_url
            # guard exists to stop. The site always comes from the saved value.
            env_arg="site",
            hint="The region your Datadog account lives in",
        ),
    ),
    connected_when=("DATADOG_API_KEY", "DATADOG_APP_KEY"),
)


def _event_url(raw: str, site: str) -> str:
    """Datadog returns the event link relative to the app host; make it absolute."""
    if raw.startswith("/"):
        chosen = site if site in SITES else DEFAULT_SITE
        return f"https://app.{chosen}{raw}"
    return raw


def fetch(window_start: datetime, window_end: datetime) -> tuple[OpsEvent, ...]:
    """Alert events Datadog raised in the window.

    The Events API, not the Monitors API: a monitor is a definition, and what a
    sprint wants to know is how often it went off. ``sources=alert`` keeps
    deploys, comments and integration chatter out. Nothing here reads a log
    line, a metric series or a dashboard — the event ``text`` field is returned
    by Datadog and deliberately never read.
    """
    from yeaboi.connectors.fetching import PAGE_LIMIT, env, read_json, rows

    api_key, app_key = env("DATADOG_API_KEY"), env("DATADOG_APP_KEY")
    base = api_base(env("DATADOG_SITE", DEFAULT_SITE))
    url = (
        f"{base}/api/v1/events"
        f"?start={int(window_start.timestamp())}&end={int(window_end.timestamp())}"
        f"&sources=alert&unaggregated=true"
    )
    body = read_json(url, headers={"DD-API-KEY": api_key, "DD-APPLICATION-KEY": app_key}, source="datadog")

    events = []
    for row in rows(body, "events")[:PAGE_LIMIT]:
        started = parse_ts(str(row.get("date_happened") or ""))
        # Datadog tags a monitor event with the service it watches; the first
        # `service:` tag is the only one that names one.
        tags = [str(t) for t in (row.get("tags") or []) if isinstance(t, str)]
        service = next((t.split(":", 1)[1] for t in tags if t.startswith("service:")), "")
        alert_type = str(row.get("alert_type") or "")
        events.append(
            OpsEvent(
                kind="alert",
                source="datadog",
                ref=str(row.get("id") or ""),
                title=clean_title(str(row.get("title") or "")),
                service=service,
                severity=clean_severity(alert_type),
                status="resolved" if alert_type in ("success", "recovery") else "firing",
                started_at=iso(started),
                url=_event_url(str(row.get("url") or ""), env("DATADOG_SITE", DEFAULT_SITE)),
            )
        )
    return tuple(events)
