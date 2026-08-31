"""Sentry — error volume, as a rate rather than an anecdote.

Two required fields (a token and the organisation slug it is scoped to) plus an
optional base URL for a self-hosted install. The base URL is an ``env_arg``
rather than a verify field: it is optional, and an optional host that a request
could set is a host a request could redirect the stored token to.
"""

from __future__ import annotations

from datetime import datetime
from urllib.parse import quote

from yeaboi.connectors.spec import Connector, ConnectorField
from yeaboi.ops.events import OpsEvent, clean_severity, clean_title, iso, parse_ts

DEFAULT_BASE_URL = "https://sentry.io"


def api_base(base_url: str) -> str:
    """The API root, defaulting to sentry.io and tolerating a trailing slash."""
    return ((base_url or "").strip() or DEFAULT_BASE_URL).rstrip("/")


CONNECTOR = Connector(
    key="sentry",
    label="Sentry",
    family="errors",
    section="connections",
    summary="New and regressed issues, so a spike is visible before the retro",
    detail=(
        "yeaboi reads issue counts by project over the window a mode already "
        "covers — how many are new, how many regressed. It never reads stack "
        "traces, breadcrumbs or event bodies, so nothing a user typed into your "
        "app can reach a yeaboi artifact, and it never resolves or assigns an issue."
    ),
    verify="_verify_sentry",
    fetch="fetch",
    docs_url="https://docs.sentry.io/account/auth-tokens/",
    accent="rgb(106,84,164)",
    fields=(
        ConnectorField(
            env="SENTRY_AUTH_TOKEN",
            label="Auth Token",
            secret=True,
            verify_arg="token",
            help_url="https://sentry.io/settings/account/api/auth-tokens/",
            help_scope="Scopes: org:read, project:read, event:read — yeaboi only ever reads",
        ),
        ConnectorField(
            env="SENTRY_ORG",
            label="Organisation Slug",
            verify_arg="org",
            placeholder="your-org",
            hint="The slug in your Sentry URL, not the display name",
        ),
        ConnectorField(
            env="SENTRY_BASE_URL",
            label="Base URL",
            required=False,
            default=DEFAULT_BASE_URL,
            # Optional, so it cannot be a verify field: verify_connection rejects
            # an unset one, and a host a request may set is a host a request may
            # redirect the stored token to.
            env_arg="base_url",
            placeholder=DEFAULT_BASE_URL,
            hint="Only for a self-hosted Sentry — leave blank for sentry.io",
        ),
    ),
    connected_when=("SENTRY_AUTH_TOKEN", "SENTRY_ORG"),
)


def _label(row: dict) -> str:
    """What to call an issue without repeating anything a user typed.

    Sentry's ``title`` is the exception class *and its message*, and that message
    routinely quotes the input that caused it. The exception type plus the
    culprit — the module and function — names the same issue and carries no
    payload; the raw title is the fallback only when neither is present.
    """
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    kind, culprit = str(metadata.get("type") or ""), str(row.get("culprit") or "")
    if kind and culprit:
        return clean_title(f"{kind} in {culprit}")
    return clean_title(kind or culprit or str(row.get("title") or ""))


def fetch(window_start: datetime, window_end: datetime) -> tuple[OpsEvent, ...]:
    """Unresolved issues Sentry first saw in the window, one event per issue.

    An issue, not an event: Sentry groups thousands of occurrences into one, and
    the group is what a sprint reasons about. No stack trace, breadcrumb or
    event body is requested or read — see :func:`_label` for what stands in for
    a name.
    """
    from yeaboi.connectors.fetching import PAGE_LIMIT, env, read_json, rows

    token, org = env("SENTRY_AUTH_TOKEN"), env("SENTRY_ORG")
    base = api_base(env("SENTRY_BASE_URL"))
    # The org slug is user-supplied and lands in the path, so it is quoted with
    # no safe characters — a slug containing a slash must not become a segment.
    url = (
        f"{base}/api/0/organizations/{quote(org, safe='')}/issues/"
        f"?query={quote('is:unresolved', safe='')}"
        f"&start={quote(iso(window_start), safe='')}&end={quote(iso(window_end), safe='')}"
        f"&utc=true&statsPeriod=&limit={PAGE_LIMIT}"
    )
    body = read_json(url, headers={"Authorization": f"Bearer {token}"}, source="sentry")

    events = []
    for row in rows(body, ""):
        project = row.get("project") if isinstance(row.get("project"), dict) else {}
        events.append(
            OpsEvent(
                kind="error_spike",
                source="sentry",
                ref=str(row.get("shortId") or row.get("id") or ""),
                title=_label(row),
                service=str(project.get("slug") or ""),
                severity=clean_severity(str(row.get("level") or "")),
                status=str(row.get("status") or ""),
                started_at=iso(parse_ts(str(row.get("firstSeen") or ""))),
                url=str(row.get("permalink") or ""),
            )
        )
    return tuple(events)
