"""Bitbucket — Pipelines outcomes for the workspace a team plans against.

Bitbucket Cloud only, so the host is fixed and every credential field can be a
verify field: the workspace is an identifier, and the email + API token pair is
the same Atlassian identity Jira uses.
"""

from __future__ import annotations

import base64
from datetime import datetime
from urllib.parse import quote

from yeaboi.connectors.spec import Connector, ConnectorField
from yeaboi.ops.events import OpsEvent, clean_title, iso, parse_ts

API_BASE = "https://api.bitbucket.org/2.0"

#: How many repositories one fetch will walk — the request bound, as GitLab's.
REPO_LIMIT = 20

#: A completed pipeline's result, and what it is worth to a sprint.
_RESULTS: dict[str, str] = {
    "SUCCESSFUL": "info",
    "FAILED": "high",
    "ERROR": "high",
    "STOPPED": "low",
}


def basic_auth(email: str, token: str) -> str:
    return base64.b64encode(f"{email}:{token}".encode()).decode()


CONNECTOR = Connector(
    key="bitbucket",
    label="Bitbucket",
    family="code",
    section="connections",
    summary="Pipelines results across the workspace, so deploy health reaches planning",
    detail=(
        "yeaboi reads completed Pipelines results — repository, branch, outcome, "
        "timing — across one workspace, over the window a mode already covers. "
        "It never reads build logs, code or pull request bodies, and it never "
        "triggers, stops or retries a pipeline."
    ),
    verify="_verify_bitbucket",
    fetch="fetch",
    docs_url="https://support.atlassian.com/bitbucket-cloud/docs/api-tokens/",
    glyph="\U0001faa3",  # 🪣 — the bucket
    accent="rgb(0,82,204)",
    fields=(
        ConnectorField(
            env="BITBUCKET_EMAIL",
            label="Atlassian Email",
            verify_arg="email",
            placeholder="you@company.com",
            hint="The email of the Atlassian account the API token belongs to",
        ),
        ConnectorField(
            env="BITBUCKET_API_TOKEN",
            label="API Token",
            secret=True,
            verify_arg="token",
            help_url="https://id.atlassian.com/manage-profile/security/api-tokens",
            help_scope="An Atlassian API token — yeaboi only ever reads",
        ),
        ConnectorField(
            env="BITBUCKET_WORKSPACE",
            label="Workspace",
            verify_arg="workspace",
            placeholder="your-workspace",
            hint="The workspace ID in your bitbucket.org URLs, not the display name",
        ),
    ),
)


def fetch(window_start: datetime, window_end: datetime) -> tuple[OpsEvent, ...]:
    """Completed pipelines across the workspace's active repositories.

    Repositories come newest-activity first and are capped at
    :data:`REPO_LIMIT`; pipelines come newest first and stop at the window
    edge, so the request count is bounded before the first call leaves. Build
    logs are never requested — a pipeline contributes its outcome only.
    """
    from yeaboi.connectors.fetching import PAGE_LIMIT, env, read_json, rows

    email, token = env("BITBUCKET_EMAIL"), env("BITBUCKET_API_TOKEN")
    workspace = quote(env("BITBUCKET_WORKSPACE"), safe="")
    headers = {"Authorization": f"Basic {basic_auth(email, token)}"}

    repos_url = f"{API_BASE}/repositories/{workspace}?role=member&sort=-updated_on&pagelen={REPO_LIMIT}"
    repos = rows(read_json(repos_url, headers=headers, source="bitbucket"), "values")

    events: list[OpsEvent] = []
    for repo in repos[:REPO_LIMIT]:
        slug = str(repo.get("slug") or "")
        if not slug:
            continue
        pipelines_url = (
            f"{API_BASE}/repositories/{workspace}/{quote(slug, safe='')}/pipelines/?sort=-created_on&pagelen=50"
        )
        for row in rows(read_json(pipelines_url, headers=headers, source="bitbucket"), "values"):
            created = parse_ts(str(row.get("created_on") or ""))
            if created is not None and created < window_start:
                break  # newest first — everything after this is older still
            state = row.get("state") if isinstance(row.get("state"), dict) else {}
            result = state.get("result") if isinstance(state.get("result"), dict) else {}
            outcome = str(result.get("name") or "")
            if outcome not in _RESULTS:
                continue
            target = row.get("target") if isinstance(row.get("target"), dict) else {}
            branch = str(target.get("ref_name") or "")
            number = row.get("build_number")
            events.append(
                OpsEvent(
                    kind="deploy",
                    source="bitbucket",
                    ref=f"{slug}#{number}" if number is not None else str(row.get("uuid") or ""),
                    title=clean_title(f"Pipeline {outcome.lower()} on {branch or '?'}"),
                    service=slug,
                    severity=_RESULTS[outcome],
                    status=outcome.lower(),
                    started_at=iso(created),
                    ended_at=iso(parse_ts(str(row.get("completed_on") or ""))),
                    url=(
                        f"https://bitbucket.org/{workspace}/{quote(slug, safe='')}/pipelines/results/{number}"
                        if number is not None
                        else ""
                    ),
                )
            )
            if len(events) >= PAGE_LIMIT:
                return tuple(events)
    return tuple(events)
