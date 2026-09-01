"""CircleCI — workflow outcomes, one org at a time.

One token plus the org slug it reads. The slug rides the query string rather
than the path, and the host is fixed — nothing here for a caller to redirect.
"""

from __future__ import annotations

from datetime import datetime
from urllib.parse import quote, urlencode

from yeaboi.connectors.spec import Connector, ConnectorField
from yeaboi.ops.events import OpsEvent, clean_title, iso, parse_ts

API_BASE = "https://circleci.com/api/v2"

#: How many pipelines one fetch will walk. Workflows are read per pipeline, so
#: a bound here is a bound on the number of requests a gather can make.
PIPELINE_LIMIT = 20

#: Workflow statuses that mean the run is over, and what each is worth to a
#: sprint. ``error`` is CircleCI's infrastructure failure — real, but not the
#: red build a failed workflow is.
_FINISHED: dict[str, str] = {
    "success": "info",
    "failed": "high",
    "error": "medium",
    "canceled": "low",
}


CONNECTOR = Connector(
    key="circleci",
    label="CircleCI",
    family="code",
    section="connections",
    summary="Workflow results across your org, so pipeline health reaches planning",
    detail=(
        "yeaboi reads finished workflow results — project, outcome, timing — "
        "for the org your token can see, over the window a mode already "
        "covers. It never reads job output, artifacts or code, and it never "
        "retries, cancels or triggers a workflow."
    ),
    verify="_verify_circleci",
    fetch="fetch",
    docs_url="https://circleci.com/docs/managing-api-tokens/",
    glyph="⭕",  # the circle
    accent="rgb(52,52,52)",
    fields=(
        ConnectorField(
            env="CIRCLECI_TOKEN",
            label="Personal API Token",
            secret=True,
            verify_arg="token",
            help_url="https://app.circleci.com/settings/user/tokens",
            help_scope="A personal API token — yeaboi only ever reads",
        ),
        ConnectorField(
            env="CIRCLECI_ORG_SLUG",
            label="Org Slug",
            verify_arg="org_slug",
            placeholder="gh/acme",
            hint="The slug from your org's URL — vcs prefix included, e.g. gh/acme",
        ),
    ),
)


def fetch(window_start: datetime, window_end: datetime) -> tuple[OpsEvent, ...]:
    """Finished workflows across the org's recent pipelines, one event per run.

    Pipelines come newest first, so the walk stops at the window edge and is
    capped at :data:`PIPELINE_LIMIT` — the request count is bounded before the
    first workflow call leaves. Job output and artifacts are never requested.
    """
    from yeaboi.connectors.fetching import PAGE_LIMIT, env, read_json, rows

    token = env("CIRCLECI_TOKEN")
    headers = {"Circle-Token": token}

    pipelines_url = f"{API_BASE}/pipeline?{urlencode({'org-slug': env('CIRCLECI_ORG_SLUG')})}"
    pipelines = rows(read_json(pipelines_url, headers=headers, source="circleci"), "items")

    events: list[OpsEvent] = []
    walked = 0
    for pipeline in pipelines:
        created = parse_ts(str(pipeline.get("created_at") or ""))
        if created and created < window_start:
            break  # newest first — everything after this is older still
        pipeline_id, project = pipeline.get("id"), str(pipeline.get("project_slug") or "")
        number = pipeline.get("number")
        if not pipeline_id:
            continue
        walked += 1
        if walked > PIPELINE_LIMIT:
            break
        workflows_url = f"{API_BASE}/pipeline/{quote(str(pipeline_id), safe='')}/workflow"
        for row in rows(read_json(workflows_url, headers=headers, source="circleci"), "items"):
            status = str(row.get("status") or "")
            if status not in _FINISHED:
                continue
            events.append(
                OpsEvent(
                    kind="deploy",
                    source="circleci",
                    ref=f"{project}#{number}",
                    title=clean_title(f"Workflow {status} on {project}"),
                    service=project,
                    severity=_FINISHED[status],
                    status=status,
                    started_at=iso(parse_ts(str(row.get("created_at") or ""))),
                    ended_at=iso(parse_ts(str(row.get("stopped_at") or ""))),
                    url=f"https://app.circleci.com/pipelines/{project}/{number}" if project and number else "",
                )
            )
            if len(events) >= PAGE_LIMIT:
                return tuple(events)
    return tuple(events)
