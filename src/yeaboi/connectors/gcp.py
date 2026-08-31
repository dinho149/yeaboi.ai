"""Google Cloud — error groups, under a token yeaboi scopes.

The recommended path is **service-account impersonation**: the ambient chain is
permitted only to mint a token for a dedicated service account, and the minted
token carries that account's roles and only the scopes named below. There is no
key-file option at all — a downloaded service-account key is a long-lived secret
on disk, and the thing Google's own guidance tells you not to create.

**Why Error Reporting and not Monitoring.** Cloud Monitoring publishes alert
*policies* — configuration — and has no public incidents API; a firing incident
only ever leaves via a notification channel. Reading a metric series to
synthesise events would put a metric series across the connector boundary, which
is exactly what that boundary is for. Error Reporting group stats are real
events: a count, the services affected, and when the group was first and last
seen.
"""

from __future__ import annotations

import logging
from datetime import datetime
from urllib.parse import quote

from yeaboi.connectors.spec import AuthMethod, Connector, ConnectorField
from yeaboi.ops.events import OpsEvent, clean_title, iso, parse_ts

logger = logging.getLogger(__name__)

API_BASE = "https://clouderrorreporting.googleapis.com/v1beta1"

#: The scopes yeaboi asks for when it mints a token. Error Reporting publishes
#: no narrower scope than cloud-platform, so this is the read-only form of it —
#: structurally incapable of a write, and asserted as such by the guard test.
#: The narrowing that IS service-level is the role the customer grants the
#: account: roles/errorreporting.viewer.
SCOPES: tuple[str, ...] = ("https://www.googleapis.com/auth/cloud-platform.read-only",)

#: How long a minted token lives. One gather.
TOKEN_SECONDS = 900

_IMPERSONATE = "impersonate"
_AMBIENT = "ambient"

CONNECTOR = Connector(
    key="gcp",
    label="Google Cloud",
    family="cloud",
    section="connections",
    summary="Error groups Cloud Error Reporting saw during the sprint",
    detail=(
        "yeaboi mints a short-lived token for a service account you create, carrying only "
        "read-only scopes, and reads Error Reporting group counts and the services they "
        "affect. It never reads a stack trace, a log line or a metric series, never writes "
        "anything back, and never attributes an error to a person. Google Cloud has no "
        "public incidents API, so this is error groups rather than alerts."
    ),
    verify="_verify_gcp",
    fetch="fetch",
    docs_url="https://cloud.google.com/iam/docs/service-account-impersonation",
    accent="rgb(66,133,244)",
    auth_env="GCP_AUTH_METHOD",
    auth_methods=(
        AuthMethod(
            key=_IMPERSONATE,
            label="Impersonate a service account",
            summary="yeaboi mints a short-lived, read-only-scoped token for an account you create.",
            recommended=True,
            setup_url="https://cloud.google.com/iam/docs/service-account-impersonation",
            envs=("GCP_SERVICE_ACCOUNT",),
        ),
        AuthMethod(
            key=_AMBIENT,
            label="This machine's credentials",
            summary="Use the application default credentials already on this machine.",
            warning="yeaboi cannot bound what this identity may do — it runs as whatever this machine is.",
            envs=(),
        ),
    ),
    fields=(
        ConnectorField(
            env="GCP_AUTH_METHOD",
            label="How to connect",
            choices=(_IMPERSONATE, _AMBIENT),
            default=_IMPERSONATE,
            env_arg="auth_method",
            hint="Impersonation is the only one yeaboi can scope",
        ),
        ConnectorField(
            env="GCP_PROJECT_ID",
            label="Project ID",
            env_arg="project_id",
            placeholder="my-project-123456",
            hint="Whose errors to read",
        ),
        ConnectorField(
            env="GCP_SERVICE_ACCOUNT",
            label="Service account",
            auth_method=_IMPERSONATE,
            env_arg="service_account",
            placeholder="yeaboi-reader@my-project.iam.gserviceaccount.com",
            help_url="https://cloud.google.com/iam/docs/service-account-impersonation",
            help_scope="Grant it roles/errorreporting.viewer, and let your own identity impersonate it",
        ),
    ),
)

PKG_MISSING = (
    "Google Cloud support isn't installed — run: uv sync --extra cloud (or: pip install 'yeaboi[cloud]'), then retry"
)


def installed() -> bool:
    """Whether google-auth is importable, without importing it."""
    import importlib.util

    return importlib.util.find_spec("google.auth") is not None


def access_token() -> str:
    """A short-lived bearer token for the chosen identity.

    Under ``impersonate`` the ambient credentials are used only to mint a token
    for the named service account, and the minted token carries that account's
    roles and ``SCOPES`` — nothing of the caller's own reach survives into it.
    """
    import google.auth
    from google.auth import impersonated_credentials
    from google.auth.transport.requests import Request

    from yeaboi.connectors.fetching import env

    source, _ = google.auth.default()
    account = env("GCP_SERVICE_ACCOUNT")
    if env("GCP_AUTH_METHOD", _IMPERSONATE) == _IMPERSONATE and account:
        creds = impersonated_credentials.Credentials(
            source_credentials=source,
            target_principal=account,
            target_scopes=list(SCOPES),
            lifetime=TOKEN_SECONDS,
        )
    else:
        logger.info("gcp: using ambient credentials (unbounded by yeaboi)")
        creds = source.with_scopes(list(SCOPES)) if hasattr(source, "with_scopes") else source
    creds.refresh(Request())
    return str(creds.token or "")


def group_stats_url(project: str, window_start: datetime, window_end: datetime) -> str:
    """The Error Reporting URL for one window.

    The API takes a coarse named period rather than two timestamps, so the
    narrowest period covering the window is requested and the results are
    filtered on ``lastSeenTime`` here — the same client-side narrowing
    incident.io already does, for the same reason.
    """
    span = (window_end - window_start).total_seconds()
    period = next(
        (name for limit, name in _PERIODS if span <= limit),
        "PERIOD_30_DAYS",
    )
    return (
        f"{API_BASE}/projects/{quote(project, safe='')}/groupStats"
        f"?timeRange.period={period}&pageSize=100&order=COUNT_DESC"
    )


#: Error Reporting's fixed windows, smallest first. A project id is a
#: user-supplied path segment, hence the quote() above.
_PERIODS: tuple[tuple[float, str], ...] = (
    (3600, "PERIOD_1_HOUR"),
    (21600, "PERIOD_6_HOURS"),
    (86400, "PERIOD_1_DAY"),
    (604800, "PERIOD_1_WEEK"),
    (2592000, "PERIOD_30_DAYS"),
)


def fetch(window_start: datetime, window_end: datetime) -> tuple[OpsEvent, ...]:
    """Error groups seen in the window — one event per GROUP, not per occurrence.

    "3 error groups affecting checkout" is a claim the data supports; "5,000
    errors" invites arithmetic against a baseline this tool does not have.

    A group is never titled by ``representative.message``: that field is a stack
    trace. The service it affects and the group's own id are what name it — the
    same call already made for Sentry, whose ``title`` embeds the exception
    message.
    """
    from yeaboi.connectors.fetching import FetchError, env, read_json, rows

    if not installed():
        raise FetchError(PKG_MISSING)

    project = env("GCP_PROJECT_ID")
    if not project:
        raise FetchError("no project id set — run `yeaboi connections add gcp`")
    try:
        token = access_token()
    except Exception as exc:
        from yeaboi.provider_verification import _connection_error

        raise FetchError(_connection_error(exc)) from None

    body = read_json(
        group_stats_url(project, window_start, window_end),
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        source="gcp",
    )

    events = []
    for row in rows(body, "errorGroupStats"):
        group = row.get("group") if isinstance(row.get("group"), dict) else {}
        group_id = str(group.get("groupId") or "")
        first = parse_ts(str(row.get("firstSeenTime") or ""))
        last = parse_ts(str(row.get("lastSeenTime") or ""))
        if last is not None and not (window_start <= last <= window_end):
            continue
        services = [
            str(s.get("service") or "")
            for s in (row.get("affectedServices") or [])
            if isinstance(s, dict) and s.get("service")
        ]
        service = services[0] if services else ""
        events.append(
            OpsEvent(
                kind="error_spike",
                source="gcp",
                ref=group_id,
                title=clean_title(f"{service} error group" if service else f"error group {group_id}"),
                service=service,
                started_at=iso(first),
                ended_at=iso(last),
                url=f"https://console.cloud.google.com/errors?project={quote(project, safe='')}",
            )
        )
    return tuple(events)
