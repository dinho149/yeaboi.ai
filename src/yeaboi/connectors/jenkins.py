"""Jenkins — build outcomes from the install a team actually runs.

The host belongs to the user, so it is a verify field and every request goes
through ``connectors.http.assert_safe_url``: https only, never an address on
this machine or a private range — which also means a Jenkins that is not
publicly reachable cannot be probed, and the field's hint says so.

One request per fetch: the ``tree`` parameter asks the root API for jobs and
their recent builds in a single round trip, bounded server-side.
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone

from yeaboi.connectors.spec import Connector, ConnectorField
from yeaboi.ops.events import OpsEvent, clean_title, iso

#: How many jobs, and how many builds per job, one fetch asks for — the bound
#: rides inside the tree parameter, so it is enforced before the reply exists.
JOB_LIMIT = 20
BUILD_LIMIT = 20

#: A completed build's result, and what it is worth to a sprint.
_RESULTS: dict[str, str] = {
    "SUCCESS": "info",
    "FAILURE": "high",
    "UNSTABLE": "medium",
    "ABORTED": "low",
}


def api_base(base_url: str) -> str:
    """The install's root, tolerating a trailing slash."""
    return (base_url or "").strip().rstrip("/")


def basic_auth(username: str, token: str) -> str:
    return base64.b64encode(f"{username}:{token}".encode()).decode()


CONNECTOR = Connector(
    key="jenkins",
    label="Jenkins",
    family="code",
    section="connections",
    summary="Build results from your own Jenkins, so pipeline health reaches planning",
    detail=(
        "yeaboi reads completed build results — job, outcome, timing — from "
        "the Jenkins you point it at, over the window a mode already covers. "
        "It never reads console output, artifacts or job configuration, and "
        "it never starts, stops or retries a build."
    ),
    verify="_verify_jenkins",
    fetch="fetch",
    docs_url="https://www.jenkins.io/doc/book/using/using-credentials/",
    glyph="\U0001f935",  # 🤵 — the butler
    accent="rgb(211,56,51)",
    fields=(
        ConnectorField(
            env="JENKINS_BASE_URL",
            label="Base URL",
            verify_arg="base_url",
            placeholder="https://ci.example.com",
            hint="Your Jenkins root — https only, and publicly reachable: a private-network install cannot be probed",
        ),
        ConnectorField(
            env="JENKINS_USER",
            label="Username",
            verify_arg="username",
            hint="The account the API token belongs to",
        ),
        ConnectorField(
            env="JENKINS_API_TOKEN",
            label="API Token",
            secret=True,
            verify_arg="token",
            help_url="https://www.jenkins.io/doc/book/using/using-credentials/",
            help_scope="A per-user API token from your Jenkins profile — yeaboi only ever reads",
        ),
    ),
)


def _ts(epoch_ms) -> datetime | None:
    """Jenkins timestamps are epoch milliseconds; a junk value is None."""
    try:
        return datetime.fromtimestamp(int(epoch_ms) / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def fetch(window_start: datetime, window_end: datetime) -> tuple[OpsEvent, ...]:
    """Completed builds in the window, one event per run, in one request.

    The ``tree`` parameter names exactly the fields read and caps both lists,
    so the request count and the reply size are bounded before the call
    leaves. Console output is never requested.
    """
    from yeaboi.connectors.fetching import PAGE_LIMIT, env, read_json, rows

    base = api_base(env("JENKINS_BASE_URL"))
    headers = {"Authorization": f"Basic {basic_auth(env('JENKINS_USER'), env('JENKINS_API_TOKEN'))}"}

    tree = f"jobs[name,url,builds[number,result,timestamp,duration,url]{{0,{BUILD_LIMIT}}}]{{0,{JOB_LIMIT}}}"
    body = read_json(f"{base}/api/json?tree={tree}", headers=headers, source="jenkins")

    events: list[OpsEvent] = []
    for job in rows(body, "jobs"):
        name = str(job.get("name") or "")
        builds = job.get("builds")
        for build in builds if isinstance(builds, list) else []:
            if not isinstance(build, dict):
                continue
            result = str(build.get("result") or "")
            if result not in _RESULTS:
                continue  # still running, or a result this vocabulary does not grade
            started = _ts(build.get("timestamp"))
            if started is None or not (window_start <= started <= window_end):
                continue
            try:
                ended = _ts(int(build.get("timestamp")) + int(build.get("duration") or 0))
            except (TypeError, ValueError):
                ended = None
            events.append(
                OpsEvent(
                    kind="deploy",
                    source="jenkins",
                    ref=f"{name}#{build.get('number')}",
                    title=clean_title(f"Build {result.lower()} on {name}"),
                    service=name,
                    severity=_RESULTS[result],
                    status=result.lower(),
                    started_at=iso(started),
                    ended_at=iso(ended),
                    url=str(build.get("url") or ""),
                )
            )
            if len(events) >= PAGE_LIMIT:
                return tuple(events)
    return tuple(events)
