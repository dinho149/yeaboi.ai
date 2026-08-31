"""GitLab — pipeline outcomes, so a red main branch is a fact not a memory.

One token plus an optional base URL for a self-hosted install. The base URL is
an ``env_arg`` rather than a verify field: it is optional, and an optional host
that a request could set is a host a request could redirect the stored token to.
"""

from __future__ import annotations

from datetime import datetime
from urllib.parse import quote

from yeaboi.connectors.spec import Connector, ConnectorField
from yeaboi.ops.events import OpsEvent, clean_title, iso, parse_ts

DEFAULT_BASE_URL = "https://gitlab.com"

#: How many projects one fetch will walk. Pipelines are read per project, and a
#: bound here is a bound on the number of requests a gather can make.
PROJECT_LIMIT = 20

#: Pipeline statuses that mean the run is over, and what each is worth to a
#: sprint. A failed pipeline is the signal; everything still moving is noise.
_FINISHED: dict[str, str] = {
    "success": "info",
    "failed": "high",
    "canceled": "low",
    "skipped": "info",
}


def api_base(base_url: str) -> str:
    """The API root, defaulting to gitlab.com and tolerating a trailing slash."""
    return ((base_url or "").strip() or DEFAULT_BASE_URL).rstrip("/")


CONNECTOR = Connector(
    key="gitlab",
    label="GitLab",
    family="code",
    section="connections",
    summary="Pipeline results across your projects, so deploy health reaches planning",
    detail=(
        "yeaboi reads finished pipeline results — project, branch, outcome, "
        "timing — for the projects your token can see, over the window a mode "
        "already covers. It never reads job logs, artifacts, code or merge "
        "request bodies, and it never retries, cancels or triggers a pipeline."
    ),
    verify="_verify_gitlab",
    fetch="fetch",
    docs_url="https://docs.gitlab.com/user/profile/personal_access_tokens/",
    accent="rgb(226,67,41)",
    fields=(
        ConnectorField(
            env="GITLAB_TOKEN",
            label="Access Token",
            secret=True,
            verify_arg="token",
            help_url="https://gitlab.com/-/user_settings/personal_access_tokens",
            help_scope="Scope: read_api — yeaboi only ever reads",
        ),
        ConnectorField(
            env="GITLAB_BASE_URL",
            label="Base URL",
            required=False,
            default=DEFAULT_BASE_URL,
            # Optional, so it cannot be a verify field: verify_connection rejects
            # an unset one, and a host a request may set is a host a request may
            # redirect the stored token to.
            env_arg="base_url",
            placeholder=DEFAULT_BASE_URL,
            hint="Only for a self-hosted GitLab — leave blank for gitlab.com",
        ),
    ),
    connected_when=("GITLAB_TOKEN",),
)


def fetch(window_start: datetime, window_end: datetime) -> tuple[OpsEvent, ...]:
    """Finished pipelines across recently active projects, one event per run.

    Projects are narrowed to those active in the window before any pipeline is
    read, and capped at :data:`PROJECT_LIMIT` — the request count is bounded
    before the first pipeline call leaves. Job logs and artifacts are never
    requested; a pipeline contributes its outcome and nothing it printed.
    """
    from yeaboi.connectors.fetching import PAGE_LIMIT, env, read_json, rows

    token = env("GITLAB_TOKEN")
    base = api_base(env("GITLAB_BASE_URL"))
    headers = {"PRIVATE-TOKEN": token}

    projects_url = (
        f"{base}/api/v4/projects"
        f"?membership=true&simple=true&archived=false"
        f"&order_by=last_activity_at&sort=desc"
        f"&last_activity_after={quote(iso(window_start), safe='')}"
        f"&per_page={PROJECT_LIMIT}"
    )
    projects = rows(read_json(projects_url, headers=headers, source="gitlab"), "")

    events: list[OpsEvent] = []
    for project in projects[:PROJECT_LIMIT]:
        project_id, path = project.get("id"), str(project.get("path_with_namespace") or "")
        if project_id is None:
            continue
        pipelines_url = (
            f"{base}/api/v4/projects/{quote(str(project_id), safe='')}/pipelines"
            f"?updated_after={quote(iso(window_start), safe='')}"
            f"&per_page={PAGE_LIMIT}"
        )
        for row in rows(read_json(pipelines_url, headers=headers, source="gitlab"), ""):
            status = str(row.get("status") or "")
            if status not in _FINISHED:
                continue
            events.append(
                OpsEvent(
                    kind="deploy",
                    source="gitlab",
                    ref=f"{path}#{row.get('iid') or row.get('id')}",
                    title=clean_title(f"Pipeline {status} on {row.get('ref') or '?'}"),
                    service=path,
                    severity=_FINISHED[status],
                    status=status,
                    started_at=iso(parse_ts(str(row.get("created_at") or ""))),
                    ended_at=iso(parse_ts(str(row.get("updated_at") or ""))),
                    url=str(row.get("web_url") or ""),
                )
            )
            if len(events) >= PAGE_LIMIT:
                return tuple(events)
    return tuple(events)
