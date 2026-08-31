"""Microsoft Azure — fired alerts, through a dedicated app registration.

Azure has no assume-role, so there is no session policy to intersect and no
honest claim of parity with AWS. The guarantee here is narrower and the
descriptor says so: a **dedicated app registration** holding one role assignment
(``Monitoring Reader``) at a scope the customer picks, and the fact that yeaboi
calls only GET endpoints. yeaboi cannot verify that assignment — reading role
assignments needs a permission Monitoring Reader does not grant.

Keyed ``azure_cloud`` rather than ``azure``: the desktop's icon table already
binds ``azure`` to Azure **DevOps**, and a connector keyed ``azure`` would
silently wear the wrong logo.

There is deliberately no ambient method and no SDK. The token exchange is a POST
to a fixed Microsoft host and ARM is Bearer REST, so both halves go through
:mod:`yeaboi.connectors.http` and inherit ``assert_safe_url`` — an SDK would
open sockets that guard never sees.
"""

from __future__ import annotations

import logging
from datetime import datetime
from urllib.parse import quote

from yeaboi.connectors.spec import Connector, ConnectorField
from yeaboi.ops.events import OpsEvent, clean_severity, clean_title, iso, parse_ts

logger = logging.getLogger(__name__)

LOGIN_HOST = "https://login.microsoftonline.com"
ARM_BASE = "https://management.azure.com"
ALERTS_API_VERSION = "2019-05-05-preview"

#: Which of the tenant's own directories the token is for. ARM's own scope, and
#: the only one requested.
ARM_SCOPE = f"{ARM_BASE}/.default"

CONNECTOR = Connector(
    key="azure_cloud",
    label="Microsoft Azure",
    family="cloud",
    section="connections",
    summary="Azure Monitor alerts that fired while the sprint ran",
    detail=(
        "yeaboi reads fired alerts — their name, severity, target resource and whether they "
        "are still firing — through an app registration you create holding only Monitoring "
        "Reader. It never reads logs, metric series or billing, never writes anything back, "
        "and never attributes an alert to a person. Azure has no assume-role, so unlike AWS "
        "yeaboi cannot narrow the credential itself: the bound is the dedicated principal "
        "and the fact that every call is a GET."
    ),
    verify="_verify_azure_cloud",
    fetch="fetch",
    docs_url="https://learn.microsoft.com/entra/identity-platform/howto-create-service-principal-portal",
    accent="rgb(0,120,212)",
    fields=(
        ConnectorField(
            env="AZURE_CLOUD_TENANT_ID",
            label="Directory (tenant) ID",
            env_arg="tenant_id",
            placeholder="00000000-0000-0000-0000-000000000000",
        ),
        ConnectorField(
            env="AZURE_CLOUD_CLIENT_ID",
            label="Application (client) ID",
            env_arg="client_id",
            placeholder="00000000-0000-0000-0000-000000000000",
            help_url="https://learn.microsoft.com/entra/identity-platform/howto-create-service-principal-portal",
            help_scope="A dedicated app registration — do not reuse one that already has write roles",
        ),
        ConnectorField(
            env="AZURE_CLOUD_CLIENT_SECRET",
            label="Client secret",
            secret=True,
            env_arg="client_secret",
            help_scope="Certificates and secrets → New client secret",
        ),
        ConnectorField(
            env="AZURE_CLOUD_SUBSCRIPTION_ID",
            label="Subscription ID",
            env_arg="subscription_id",
            placeholder="00000000-0000-0000-0000-000000000000",
            help_scope="Grant the app Monitoring Reader on this subscription, or on a narrower scope inside it",
        ),
    ),
)


def token_url(tenant_id: str) -> str:
    """The token endpoint for a tenant. Fixed Microsoft host, user-supplied path."""
    return f"{LOGIN_HOST}/{quote(tenant_id, safe='')}/oauth2/v2.0/token"


def alerts_url(subscription_id: str, window_start: datetime, window_end: datetime) -> str:
    """The Alerts Management URL for one window.

    ``customTimeRange`` takes the mode's own window, so a report on a finished
    sprint reads that sprint rather than a lookback from now.
    """
    span = f"{iso(window_start)}/{iso(window_end)}"
    return (
        f"{ARM_BASE}/subscriptions/{quote(subscription_id, safe='')}"
        f"/providers/Microsoft.AlertsManagement/alerts"
        f"?api-version={ALERTS_API_VERSION}&customTimeRange={quote(span, safe='/:')}&pageCount=100"
    )


def access_token() -> str:
    """A bearer token for ARM, via the client-credentials grant.

    The one POST in the whole connector layer, and it goes through the same
    guard as every GET.
    """
    from yeaboi.connectors.fetching import FetchError, env
    from yeaboi.connectors.http import UnsafeUrlError, post_form

    try:
        resp = post_form(
            token_url(env("AZURE_CLOUD_TENANT_ID")),
            data={
                "grant_type": "client_credentials",
                "client_id": env("AZURE_CLOUD_CLIENT_ID"),
                "client_secret": env("AZURE_CLOUD_CLIENT_SECRET"),
                "scope": ARM_SCOPE,
            },
        )
    except UnsafeUrlError as exc:
        raise FetchError(str(exc)) from None
    except Exception as exc:
        from yeaboi.provider_verification import _connection_error

        raise FetchError(_connection_error(exc)) from None
    if resp.status_code != 200:
        raise FetchError("credentials rejected — re-run `yeaboi connections verify azure_cloud`")
    try:
        token = str((resp.json() or {}).get("access_token") or "")
    except Exception:
        raise FetchError("token response was not JSON") from None
    if not token:
        raise FetchError("the token response carried no access token")
    return token


#: Azure's monitor conditions. "Resolved" is the only one that means it is over.
_CLOSED = frozenset({"resolved"})


def fetch(window_start: datetime, window_end: datetime) -> tuple[OpsEvent, ...]:
    """Alerts Azure Monitor fired in the window.

    ``essentials`` carries the alert's identity — rule name, severity, target
    resource, timestamps. The alert's *context* (the condition that tripped and
    the values that tripped it) is a body and is never read.
    """
    from yeaboi.connectors.fetching import env, read_json, rows

    token = access_token()
    body = read_json(
        alerts_url(env("AZURE_CLOUD_SUBSCRIPTION_ID"), window_start, window_end),
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        source="azure_cloud",
    )

    portal = "https://portal.azure.com/#blade/Microsoft_Azure_Monitoring/AlertDetails/alertId"
    events = []
    for row in rows(body, "value"):
        props = row.get("properties") if isinstance(row.get("properties"), dict) else {}
        core = props.get("essentials") if isinstance(props.get("essentials"), dict) else {}
        condition = str(core.get("monitorCondition") or "").lower()
        started = parse_ts(str(core.get("startDateTime") or ""))
        events.append(
            OpsEvent(
                kind="alert",
                source="azure_cloud",
                ref=str(row.get("name") or ""),
                title=clean_title(str(core.get("alertRule") or row.get("name") or "")),
                service=str(core.get("targetResourceName") or ""),
                severity=clean_severity(str(core.get("severity") or "")),
                status="resolved" if condition in _CLOSED else condition or "firing",
                started_at=iso(started),
                ended_at=iso(parse_ts(str(core.get("monitorConditionResolvedDateTime") or ""))),
                url=f"{portal}/{row.get('id', '')}",
            )
        )
    return tuple(events)
