"""LaunchDarkly — flag changes, because a rollout is a deploy.

One access token, one fixed host. The audit log is the read: what changed and
when, filtered to flags. Who flipped the flag is in the entry too, and is
deliberately never read — a rollout is a team's act, not a person's.
"""

from __future__ import annotations

from datetime import datetime

from yeaboi.connectors.spec import Connector, ConnectorField
from yeaboi.ops.events import OpsEvent, clean_title, iso

API_BASE = "https://app.launchdarkly.com/api/v2"
SITE_BASE = "https://app.launchdarkly.com"


CONNECTOR = Connector(
    key="launchdarkly",
    label="LaunchDarkly",
    family="delivery",
    section="connections",
    summary="Flag rollouts during the sprint — what actually shipped, and when",
    detail=(
        "yeaboi reads the audit log's flag entries — which flag changed and "
        "when — over the window a mode already covers. It never reads flag "
        "rules, targeting or member identities, never flips a flag, and never "
        "attributes a change to a person."
    ),
    verify="_verify_launchdarkly",
    fetch="fetch",
    docs_url="https://launchdarkly.com/docs/home/account/api-create",
    glyph="\U0001f6a9",  # 🚩 — the feature flag
    accent="rgb(64,91,255)",
    fields=(
        ConnectorField(
            env="LAUNCHDARKLY_API_KEY",
            label="API Access Token",
            secret=True,
            verify_arg="token",
            help_url="https://app.launchdarkly.com/settings/authorization",
            help_scope="An access token with the Reader role — yeaboi only ever reads",
        ),
    ),
)


def fetch(window_start: datetime, window_end: datetime) -> tuple[OpsEvent, ...]:
    """Flag entries from the audit log in the window, one event per change.

    ``after``/``before`` are the vendor's own window parameters (epoch
    milliseconds), so the filtering happens at their end; non-flag entries —
    members, projects, tokens — are dropped here without being read further.
    """
    from yeaboi.connectors.fetching import PAGE_LIMIT, env, read_json, rows

    url = (
        f"{API_BASE}/auditlog"
        f"?after={int(window_start.timestamp() * 1000)}"
        f"&before={int(window_end.timestamp() * 1000)}"
        f"&limit={PAGE_LIMIT}"
    )
    body = read_json(url, headers={"Authorization": env("LAUNCHDARKLY_API_KEY")}, source="launchdarkly")

    events: list[OpsEvent] = []
    for row in rows(body, "items"):
        if str(row.get("kind") or "") != "flag":
            continue
        name = str(row.get("name") or "")
        links = row.get("_links") if isinstance(row.get("_links"), dict) else {}
        site = links.get("site") if isinstance(links.get("site"), dict) else {}
        href = str(site.get("href") or "")
        events.append(
            OpsEvent(
                kind="deploy",
                source="launchdarkly",
                ref=str(row.get("_id") or ""),
                title=clean_title(f"Flag changed: {name}"),
                service=name,
                severity="info",
                status=str(row.get("titleVerb") or "changed"),
                started_at=iso(_ts(row.get("date"))),
                url=f"{SITE_BASE}{href}" if href.startswith("/") else "",
            )
        )
        if len(events) >= PAGE_LIMIT:
            break
    return tuple(events)


def _ts(epoch_ms) -> datetime | None:
    """Audit-log dates are epoch milliseconds; a junk value is None."""
    from datetime import timezone

    try:
        return datetime.fromtimestamp(int(epoch_ms) / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        return None
