"""Azure DevOps tools for fetching repo context and creating work items.

# See docs: "Tools" — tool types, @tool decorator, risk levels
#
# Read tools (low risk) — fetch data from the Azure DevOps REST API and return
# it as a string for the LLM to reason about. Write tools (high risk) — create
# work items and require user confirmation before invocation.
#
# Why azure-devops SDK instead of raw requests?
# The SDK wraps the REST API with typed objects, handles authentication via
# BasicAuthentication (PAT), and raises AzureDevOpsServiceError for API
# failures. This makes error handling predictable across all tools.
#
# URL format supported (modern only):
#   https://dev.azure.com/{org}/{project}/_git/{repo}
"""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC
from types import SimpleNamespace
from urllib.parse import quote

from azure.devops.exceptions import AzureDevOpsServiceError
from langchain_core.tools import tool

from yeaboi.config import (
    get_azure_devops_org_url,
    get_azure_devops_project,
    get_azure_devops_team,
    get_azure_devops_token,
)

logger = logging.getLogger(__name__)
_AZDO_DETAIL_SEMAPHORE = threading.BoundedSemaphore(6)

# Truncate file content at this many characters to avoid flooding the LLM context.
_MAX_CONTENT_CHARS = 8_000

# Valid Azure DevOps work-item states, canonical casing. Used to whitelist the
# LLM/tool-controlled `state` before it is interpolated into a WIQL query (WIQL
# offers no bind parameters, so allowlisting is the injection defense).
_VALID_WORK_ITEM_STATES = {
    "active": "Active",
    "new": "New",
    "resolved": "Resolved",
    "closed": "Closed",
    "done": "Done",
    "removed": "Removed",
    "all": "All",
}
_DEFAULT_WORK_ITEM_STATE = "Active"


def _normalize_work_item_state(state: str) -> str:
    """Map `state` to a known canonical state, falling back to the default.

    Anything not in the whitelist (including injection attempts like
    ``Active' OR '1'='1``) is rejected and coerced to ``Active`` so it can never
    reach the WIQL string as attacker-controlled text.
    """
    canonical = _VALID_WORK_ITEM_STATES.get(str(state).strip().lower())
    if canonical is None:
        logger.warning("azdevops: ignoring unrecognized work-item state %r; using %s", state, _DEFAULT_WORK_ITEM_STATE)
        return _DEFAULT_WORK_ITEM_STATE
    return canonical


# Key config/manifest files to highlight in the repo tree summary.
# See docs: "Tools" — scoping tool output for LLM relevance
_KEY_FILES = {
    "package.json",
    "pyproject.toml",
    "setup.py",
    "Cargo.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    "README.md",
    "README.rst",
    "CONTRIBUTING.md",
    "Makefile",
    "requirements.txt",
    ".env.example",
    "tsconfig.json",
    "webpack.config.js",
    "vite.config.ts",
    "vite.config.js",
}


def _raise_if_azdo_auth(e: Exception) -> None:
    """Re-raise an Azure DevOps 401/403 as a StandupSourceError so the standup surfaces it."""
    msg = str(e).lower()
    if any(t in msg for t in ("401", "unauthorized", "403", "forbidden", "access denied")):
        from yeaboi.standup.errors import StandupSourceError

        raise StandupSourceError("azure_devops", "authentication failed — check AZURE_DEVOPS_TOKEN permissions")


def _azdo_error_msg(e: Exception) -> str:
    """Return a user-friendly message for common AzDO HTTP error codes."""
    msg = str(e).lower()
    if "401" in msg or "unauthorized" in msg:
        return "Error: Authentication failed. Check your AZURE_DEVOPS_TOKEN in .env."
    if "403" in msg or "forbidden" in msg or "access denied" in msg:
        return "Error: Access denied. Ensure your PAT has Code=Read and Work Items=Read permissions."
    if "404" in msg or "not found" in msg:
        return f"Error: Resource not found — verify the repo URL. ({e})"
    if "429" in msg or "503" in msg or "throttl" in msg:
        return "Error: Azure DevOps is throttling requests. Wait a moment and try again."
    return f"Error: {e}"


def _parse_azdo_url(url: str) -> tuple[str, str, str]:
    """Parse 'https://dev.azure.com/{org}/{project}/_git/{repo}' into (org_url, project, repo).

    Returns:
        (org_url, project, repo) — e.g. ("https://dev.azure.com/myorg", "MyProject", "my-repo")

    Raises:
        ValueError: if URL does not match the expected format.
    """
    url = url.strip().rstrip("/")
    if url.endswith(".git"):
        url = url[:-4]

    if "dev.azure.com/" not in url:
        raise ValueError(
            f"URL must be a modern Azure DevOps URL (https://dev.azure.com/org/project/_git/repo). Got: {url!r}"
        )

    # Split off everything after "dev.azure.com/" → "org/project/_git/repo"
    after = url.split("dev.azure.com/", 1)[1]
    parts = after.split("/")

    # Expect exactly: [org, project, "_git", repo] (may have extra segments — we ignore them)
    if len(parts) < 4 or parts[2] != "_git":
        raise ValueError(
            f"URL must follow the pattern https://dev.azure.com/{{org}}/{{project}}/_git/{{repo}}. Got: {url!r}"
        )

    org, project, repo = parts[0], parts[1], parts[3]

    if not org or not project or not repo:
        raise ValueError(f"org, project, and repo must be non-empty. Got: {url!r}")

    return f"https://dev.azure.com/{org}", project, repo


def _make_connection(org_url: str, token: str | None):
    """Create an authenticated Azure DevOps Connection.

    Uses BasicAuthentication with a PAT (Personal Access Token). The convention
    for AzDO PATs is an empty username and the PAT as the password. Without a
    token the connection is unauthenticated — private projects return 401/403,
    caught by the caller's error handler.

    # See docs: "Tools" — authentication pattern
    """
    from azure.devops.connection import Connection
    from msrest.authentication import BasicAuthentication

    if not token:
        logger.warning("No AZURE_DEVOPS_TOKEN set — private repos will fail")
    logger.debug("Creating AzDO connection for %s", org_url)
    creds = BasicAuthentication("", token or "")
    return Connection(base_url=org_url, creds=creds)


def _pin_client_base_url(client, org_url: str | None):
    """Keep an SDK client on the configured org URL and return it.

    ``Connection.clients.get_*_client()`` resolves the organisation's advertised
    resource location, which for visualstudio.com-era organisations is the
    legacy ``https://{org}.visualstudio.com`` alias — a host that can fail DNS
    on some networks even when ``dev.azure.com`` resolves fine. dev.azure.com
    serves the same REST surface, so every client is pinned back to the URL the
    user actually configured.
    """
    try:
        if org_url:
            wanted = org_url.rstrip("/")
            if str(getattr(client.config, "base_url", "") or "").rstrip("/") != wanted:
                client.config.base_url = wanted
    except Exception:  # defensive: never let pinning break client creation
        logger.debug("Could not pin AzDO client base URL", exc_info=True)
    return client


@tool
def azdevops_read_repo(repo_url: str, max_depth: int = 2) -> str:
    """Read the repository file tree from an Azure DevOps repository.

    Returns top-level directory structure (up to max_depth), detected tech stack
    files (package.json, pyproject.toml, Dockerfile, etc.), and repo stats.
    Use this first to understand a project's structure before reading individual files.
    """
    # See docs: "The ReAct Loop" — this is the Action step; the result is the Observation
    logger.debug("azdevops_read_repo called: repo_url=%r, max_depth=%d", repo_url, max_depth)
    try:
        org_url, project, repo = _parse_azdo_url(repo_url)
        conn = _make_connection(org_url, get_azure_devops_token())
        git_client = _pin_client_base_url(conn.clients.get_git_client(), org_url)

        # get_items with recursion_level="full" fetches the entire tree in one API call.
        # Each GitItem has .path (e.g. "/src/main.py") and .git_object_type ("blob"/"tree").
        items = git_client.get_items(repository_id=repo, project=project, recursion_level="full") or []

        lines: list[str] = [f"Repository: {project}/{repo}", f"Organization: {org_url}", ""]

        key_files_found: list[str] = []
        top_level_entries: set[str] = set()

        for item in items:
            path = item.path.lstrip("/")
            if not path:
                continue  # Skip the root entry that AzDO includes

            parts = path.split("/")
            name = parts[-1]

            if len(parts) == 1:
                top_level_entries.add(path)

            # Highlight key config/manifest files regardless of depth
            if name in _KEY_FILES or path in _KEY_FILES:
                key_files_found.append(path)

        lines.append("File tree (top level):")
        for entry in sorted(top_level_entries)[:50]:  # cap at 50 top-level entries
            lines.append(f"  {entry}")

        if key_files_found:
            lines.append("")
            lines.append("Key files detected:")
            for kf in sorted(key_files_found):
                lines.append(f"  {kf}")

        total_files = sum(1 for i in items if i.git_object_type == "blob")
        lines.append("")
        lines.append(f"Total files: {total_files}")

        logger.debug("azdevops_read_repo completed for %s/%s (%d files)", project, repo, total_files)
        return "\n".join(lines)

    except ValueError as e:
        return f"Error: {e}"
    except AzureDevOpsServiceError as e:
        logger.error("AzDO API error in azdevops_read_repo: %s", e)
        return _azdo_error_msg(e)
    except Exception as e:
        logger.error("Unexpected error in azdevops_read_repo: %s", e)
        return f"Error: {e}"


@tool
def azdevops_read_file(repo_url: str, file_path: str) -> str:
    """Fetch the raw contents of a specific file from an Azure DevOps repository.

    Use this after azdevops_read_repo identifies an important file. Truncates at
    8 000 characters with a note if the file is larger.
    """
    logger.debug("azdevops_read_file called: repo=%r, path=%r", repo_url, file_path)
    try:
        org_url, project, repo = _parse_azdo_url(repo_url)
        conn = _make_connection(org_url, get_azure_devops_token())
        git_client = _pin_client_base_url(conn.clients.get_git_client(), org_url)

        # get_item_content returns a generator of bytes chunks — join and decode.
        chunks = git_client.get_item_content(repository_id=repo, project=project, path=file_path)
        raw = b"".join(chunks)
        content = raw.decode("utf-8", errors="replace")

        truncated = False
        if len(content) > _MAX_CONTENT_CHARS:
            content = content[:_MAX_CONTENT_CHARS]
            truncated = True

        logger.debug("azdevops_read_file fetched %s (%d bytes)", file_path, len(raw))
        header = f"File: {file_path} ({len(raw)} bytes)\n\n"
        suffix = f"\n\n[Truncated at {_MAX_CONTENT_CHARS} characters]" if truncated else ""
        return header + content + suffix

    except ValueError as e:
        return f"Error: {e}"
    except AzureDevOpsServiceError as e:
        logger.error("AzDO API error in azdevops_read_file: %s", e)
        return _azdo_error_msg(e)
    except Exception as e:
        logger.error("Unexpected error in azdevops_read_file: %s", e)
        return f"Error: {e}"


@tool
def azdevops_list_work_items(repo_url: str, max_items: int = 20, state: str = "Active") -> str:
    """List work items (tasks, bugs, user stories) from an Azure DevOps project.

    Returns work item ID, type, title, state, and assigned-to for up to max_items.
    Use this to understand current backlog and in-progress work to inform the scrum plan.
    state: 'Active' (default), 'New', 'Resolved', 'Closed', or 'All' (skips state filter).
    """
    logger.debug("azdevops_list_work_items called: repo=%r, state=%s", repo_url, state)
    try:
        # Wiql is the Azure DevOps query language — SQL-like syntax for querying work items.
        # Imported here (lazy) to follow the same pattern as other tool imports.
        # See docs: "Tools" — tool types, read-only tool pattern
        from azure.devops.v7_1.work_item_tracking.models import Wiql

        org_url, project, _ = _parse_azdo_url(repo_url)
        conn = _make_connection(org_url, get_azure_devops_token())
        wit_client = _pin_client_base_url(conn.clients.get_work_item_tracking_client(), org_url)

        # SECURITY: `state` is an LLM/tool-controlled parameter and `project` is parsed from an
        # LLM-supplied URL, both interpolated into a WIQL query. WIQL has no bind-parameter API, so
        # we defend by (a) whitelisting `state` against the known enum — a value like
        # "Active' OR '1'='1" is not in the set and is coerced to the safe default — and (b) escaping
        # single quotes in `project` per WIQL rules (a quote is escaped by doubling it).
        state = _normalize_work_item_state(state)
        safe_project = project.replace("'", "''")
        # Omit the state clause when state='All' so all states are returned.
        state_clause = f" AND [System.State] = '{state}'" if state != "All" else ""
        # WIQL is read-only; `state` is whitelisted and `project` escaped above, so this f-string
        # cannot be steered. WIQL has no bind-parameter API, hence the suppression.
        wiql = Wiql(
            query=(
                f"SELECT [System.Id] FROM WorkItems"  # noqa: S608
                f" WHERE [System.TeamProject] = '{safe_project}'{state_clause}"
                f" ORDER BY [System.ChangedDate] DESC"
            )
        )

        # query_by_wiql returns a WorkItemQueryResult with .work_items = list of refs (id + url only).
        result = wit_client.query_by_wiql(wiql, top=max_items)

        if not result.work_items:
            return f"No work items found in project '{project}' with state='{state}'."

        ids = [wi.id for wi in result.work_items]
        fields = ["System.Id", "System.WorkItemType", "System.Title", "System.State", "System.AssignedTo"]

        # get_work_items fetches full field data for each ID in one batch call.
        work_items = wit_client.get_work_items(ids, fields=fields)

        lines: list[str] = [f"Work items for project '{project}' (state={state}):", ""]
        for item in work_items:
            f = item.fields
            wi_id = f.get("System.Id", "?")
            wi_type = f.get("System.WorkItemType", "?")
            wi_title = f.get("System.Title", "?")
            wi_state = f.get("System.State", "?")
            assigned_raw = f.get("System.AssignedTo")

            # AssignedTo is a dict with displayName in newer API versions, or a plain string/None.
            if isinstance(assigned_raw, dict):
                assignee = assigned_raw.get("displayName", "Unassigned")
            elif assigned_raw:
                assignee = str(assigned_raw)
            else:
                assignee = "Unassigned"

            lines.append(f"#{wi_id} [{wi_type}] {wi_title} | State: {wi_state} | Assigned: {assignee}")

        logger.debug("azdevops_list_work_items returned %d items for %s", len(work_items), project)
        note = "; increase max_items to see more" if len(work_items) >= max_items else ""
        lines.append("")
        lines.append(f"({len(work_items)} work items shown{note})")
        return "\n".join(lines)

    except ValueError as e:
        return f"Error: {e}"
    except AzureDevOpsServiceError as e:
        logger.error("AzDO API error in azdevops_list_work_items: %s", e)
        return _azdo_error_msg(e)
    except Exception as e:
        logger.error("Unexpected error in azdevops_list_work_items: %s", e)
        return f"Error: {e}"


# ---------------------------------------------------------------------------
# Board / Velocity / Iteration tools (use org-level config, not repo URL)
# ---------------------------------------------------------------------------


def _make_azdo_clients(org_url: str | None = None, token: str | None = None):
    """Create authenticated WIT and Work clients from a single connection.

    Returns (wit_client, work_client). Uses config defaults when args are None.
    # See docs: "Tools" — authentication pattern
    """
    org_url = org_url or get_azure_devops_org_url()
    token = token or get_azure_devops_token()
    if not org_url:
        raise ValueError("AZURE_DEVOPS_ORG_URL is not set. Add it to your .env file.")
    conn = _make_connection(org_url, token)
    wit_client = _pin_client_base_url(conn.clients.get_work_item_tracking_client(), org_url)
    work_client = _pin_client_base_url(conn.clients.get_work_client(), org_url)
    return wit_client, work_client


@tool
def azdevops_read_board(project: str = "") -> str:
    """Read board info from an Azure DevOps project: active iteration, backlog count, and average velocity.

    Returns the current iteration name, number of backlog items, and average velocity
    computed from the last 3 completed iterations. Use this to understand the team's
    current capacity and throughput before planning sprints.
    """
    project = project or get_azure_devops_project() or ""
    if not project:
        return "Error: No project specified. Set AZURE_DEVOPS_PROJECT in .env or pass project parameter."

    logger.debug("azdevops_read_board called: project=%r", project)
    try:
        from azure.devops.v7_1.work.models import TeamContext

        _, work_client = _make_azdo_clients()
        team = get_azure_devops_team() or f"{project} Team"
        team_context = TeamContext(project=project, team=team)

        lines: list[str] = [f"Azure DevOps Board: {project}", f"Team: {team}", ""]

        # Fetch all team iterations and classify by date
        from datetime import datetime as _dt

        all_iterations = work_client.get_team_iterations(team_context) or []
        now = _dt.now(UTC)
        current_iter = None
        past_iters: list = []

        for it in all_iterations:
            attrs = getattr(it, "attributes", None)
            start = getattr(attrs, "start_date", None) if attrs else None
            end = getattr(attrs, "finish_date", None) if attrs else None
            if start and end:
                if start <= now <= end:
                    current_iter = it
                elif end < now:
                    past_iters.append(it)

        # Current iteration
        if current_iter:
            attrs = current_iter.attributes
            start = getattr(attrs, "start_date", None)
            end = getattr(attrs, "finish_date", None)
            start_str = start.strftime("%Y-%m-%d") if start else "?"
            end_str = end.strftime("%Y-%m-%d") if end else "?"
            lines.append(f"Active iteration: {current_iter.name} ({start_str} to {end_str})")
        else:
            lines.append("Active iteration: None")

        # Past iterations for velocity (last 3)
        try:
            recent = past_iters[-3:]
            total_points = 0.0
            iter_count = 0

            wit_client = _make_azdo_clients()[0]
            for iteration in recent:
                iter_id = iteration.id
                try:
                    work_items = work_client.get_iteration_work_items(team_context, iter_id)
                    wi_ids = []
                    for relation in getattr(work_items, "work_item_relations", []) or []:
                        target = getattr(relation, "target", None)
                        if target:
                            wi_ids.append(target.id)
                    if wi_ids:
                        items = wit_client.get_work_items(
                            wi_ids,
                            fields=[
                                "System.State",
                                "Microsoft.VSTS.Scheduling.StoryPoints",
                            ],
                        )
                        for item in items or []:
                            state = item.fields.get("System.State", "")
                            if state in ("Closed", "Done", "Resolved", "Completed"):
                                pts = item.fields.get("Microsoft.VSTS.Scheduling.StoryPoints")
                                if pts:
                                    total_points += float(pts)
                        iter_count += 1
                except Exception as e:
                    logger.warning("Could not fetch iteration %s work items: %s", iteration.name, e)

            if iter_count > 0:
                avg_velocity = total_points / iter_count
                lines.append(f"Average velocity (last {iter_count} iterations): {avg_velocity:.1f} points")
                lines.append(f"Total completed points: {total_points:.0f}")
            else:
                lines.append("Velocity: No completed iteration data available")
        except Exception as e:
            logger.warning("Could not fetch past iterations: %s", e)
            lines.append(f"Velocity: Error ({e})")

        return "\n".join(lines)

    except ValueError as e:
        return f"Error: {e}"
    except AzureDevOpsServiceError as e:
        logger.error("AzDO API error in azdevops_read_board: %s", e)
        return _azdo_error_msg(e)
    except Exception as e:
        logger.error("Unexpected error in azdevops_read_board: %s", e)
        return f"Error: {e}"


@tool
def azdevops_fetch_velocity(project: str = "") -> str:
    """Fetch team velocity data from Azure DevOps: average points, team size, per-developer velocity.

    Computes velocity from the last 3 completed iterations and team size from unique
    assignees on completed items. Returns structured data for capacity planning.
    """
    project = project or get_azure_devops_project() or ""
    if not project:
        return "Error: No project specified. Set AZURE_DEVOPS_PROJECT in .env or pass project parameter."

    logger.debug("azdevops_fetch_velocity called: project=%r", project)
    try:
        from azure.devops.v7_1.work.models import TeamContext

        wit_client, work_client = _make_azdo_clients()
        team = get_azure_devops_team() or f"{project} Team"
        team_context = TeamContext(project=project, team=team)

        # Fetch all iterations and filter to past (finished before now) by date.
        # The timeframe="past" parameter is not supported by all AzDO API versions.
        from datetime import datetime as _dt

        all_iterations = work_client.get_team_iterations(team_context) or []
        now = _dt.now(UTC)
        past_iterations = [
            it
            for it in all_iterations
            if getattr(getattr(it, "attributes", None), "finish_date", None) and it.attributes.finish_date < now
        ]
        recent = past_iterations[-3:]

        total_points = 0.0
        iter_count = 0
        assignees: set[str] = set()

        for iteration in recent:
            iter_id = iteration.id
            try:
                work_items = work_client.get_iteration_work_items(team_context, iter_id)
                wi_ids = []
                for relation in getattr(work_items, "work_item_relations", []) or []:
                    target = getattr(relation, "target", None)
                    if target:
                        wi_ids.append(target.id)
                if wi_ids:
                    items = wit_client.get_work_items(
                        wi_ids,
                        fields=[
                            "System.State",
                            "Microsoft.VSTS.Scheduling.StoryPoints",
                            "System.AssignedTo",
                        ],
                    )
                    for item in items or []:
                        state = item.fields.get("System.State", "")
                        if state in ("Closed", "Done", "Resolved", "Completed"):
                            pts = item.fields.get("Microsoft.VSTS.Scheduling.StoryPoints")
                            if pts:
                                total_points += float(pts)
                            assigned = item.fields.get("System.AssignedTo")
                            if isinstance(assigned, dict):
                                name = assigned.get("uniqueName") or assigned.get("displayName", "")
                            elif assigned:
                                name = str(assigned)
                            else:
                                name = ""
                            if name:
                                assignees.add(name)
                    iter_count += 1
            except Exception as e:
                logger.warning("Could not fetch iteration %s: %s", iteration.name, e)

        if iter_count == 0:
            return "No completed iteration data available for velocity calculation."

        avg_velocity = total_points / iter_count
        team_size = len(assignees) or 1
        per_dev = avg_velocity / team_size

        lines = [
            f"Team velocity: {avg_velocity:.1f} points/iteration (avg of {iter_count} iterations)",
            f"Team size: {team_size} (unique assignees on completed items)",
            f"Per-developer velocity: {per_dev:.1f} points/iteration",
        ]
        return "\n".join(lines)

    except ValueError as e:
        return f"Error: {e}"
    except AzureDevOpsServiceError as e:
        logger.error("AzDO API error in azdevops_fetch_velocity: %s", e)
        return _azdo_error_msg(e)
    except Exception as e:
        logger.error("Unexpected error in azdevops_fetch_velocity: %s", e)
        return f"Error: {e}"


@tool
def azdevops_fetch_active_iteration(project: str = "") -> str:
    """Fetch the active (current) iteration from Azure DevOps.

    Returns sprint number, sprint name, and start date of the current iteration.
    Use this to determine the team's current sprint for planning purposes.
    """
    project = project or get_azure_devops_project() or ""
    if not project:
        return "Error: No project specified. Set AZURE_DEVOPS_PROJECT in .env or pass project parameter."

    logger.debug("azdevops_fetch_active_iteration called: project=%r", project)
    try:
        import re as _re

        from azure.devops.v7_1.work.models import TeamContext

        _, work_client = _make_azdo_clients()
        team = get_azure_devops_team() or f"{project} Team"
        team_context = TeamContext(project=project, team=team)

        # Find the current iteration by date (timeframe="current" not supported
        # by all AzDO API versions).
        from datetime import datetime as _dt

        all_iterations = work_client.get_team_iterations(team_context) or []
        now = _dt.now(UTC)
        current_iterations = [
            it
            for it in all_iterations
            if getattr(getattr(it, "attributes", None), "start_date", None)
            and getattr(it.attributes, "finish_date", None)
            and it.attributes.start_date <= now <= it.attributes.finish_date
        ]
        if not current_iterations:
            return "No active iteration found."

        cur = current_iterations[0]
        attrs = cur.attributes
        start = getattr(attrs, "start_date", None)
        start_str = start.strftime("%Y-%m-%d") if start else ""

        # Extract sprint number from name (e.g. "Sprint 42" → 42)
        match = _re.search(r"(\d+)\s*$", cur.name or "")
        sprint_number = int(match.group(1)) if match else 0

        lines = [
            f"Sprint name: {cur.name}",
            f"Sprint number: {sprint_number}",
            f"Start date: {start_str}",
        ]
        return "\n".join(lines)

    except ValueError as e:
        return f"Error: {e}"
    except AzureDevOpsServiceError as e:
        logger.error("AzDO API error in azdevops_fetch_active_iteration: %s", e)
        return _azdo_error_msg(e)
    except Exception as e:
        logger.error("Unexpected error in azdevops_fetch_active_iteration: %s", e)
        return f"Error: {e}"


# ---------------------------------------------------------------------------
# Write tools — create work items (require user confirmation)
# ---------------------------------------------------------------------------


@tool
def azdevops_create_epic(title: str, description: str = "", project: str = "") -> str:
    """Create an Epic work item in Azure DevOps. Only call after user confirms.

    Creates a top-level Epic with the given title and description. Returns the
    work item ID on success.
    """
    project = project or get_azure_devops_project() or ""
    if not project:
        return "Error: No project specified. Set AZURE_DEVOPS_PROJECT in .env or pass project parameter."

    logger.debug("azdevops_create_epic called: title=%r, project=%r", title, project)
    try:
        from azure.devops.v7_1.work_item_tracking.models import JsonPatchOperation

        wit_client = _make_azdo_clients()[0]

        document = [
            JsonPatchOperation(op="add", path="/fields/System.Title", value=title),
            JsonPatchOperation(op="add", path="/fields/System.Description", value=description),
        ]

        work_item = wit_client.create_work_item(document=document, project=project, type="Epic")
        wi_id = str(work_item.id)
        logger.info("Created AzDO Epic: %s (ID: %s)", title, wi_id)
        return f"Created Epic '{title}' — Work Item ID: {wi_id}"

    except AzureDevOpsServiceError as e:
        logger.error("AzDO API error in azdevops_create_epic: %s", e)
        return _azdo_error_msg(e)
    except Exception as e:
        logger.error("Unexpected error in azdevops_create_epic: %s", e)
        return f"Error: {e}"


@tool
def azdevops_create_story(
    summary: str,
    epic_id: str = "",
    story_points: int = 0,
    priority: int = 3,
    description: str = "",
    project: str = "",
) -> str:
    """Create a User Story work item in Azure DevOps. Only call after user confirms.

    Creates a User Story linked to a parent Epic (if epic_id is provided).
    Priority: 1=Critical, 2=High, 3=Medium, 4=Low.
    Returns the work item ID on success.
    """
    project = project or get_azure_devops_project() or ""
    if not project:
        return "Error: No project specified. Set AZURE_DEVOPS_PROJECT in .env or pass project parameter."

    logger.debug("azdevops_create_story called: summary=%r, epic_id=%r, project=%r", summary, epic_id, project)
    try:
        from azure.devops.v7_1.work_item_tracking.models import JsonPatchOperation

        wit_client = _make_azdo_clients()[0]

        document = [
            JsonPatchOperation(op="add", path="/fields/System.Title", value=summary),
            JsonPatchOperation(op="add", path="/fields/System.Description", value=description),
            JsonPatchOperation(
                op="add",
                path="/fields/Microsoft.VSTS.Common.Priority",
                value=priority,
            ),
        ]

        if story_points > 0:
            document.append(
                JsonPatchOperation(
                    op="add",
                    path="/fields/Microsoft.VSTS.Scheduling.StoryPoints",
                    value=float(story_points),
                )
            )

        # Link to parent Epic via System.LinkTypes.Hierarchy-Reverse
        if epic_id:
            org_url = get_azure_devops_org_url() or ""
            document.append(
                JsonPatchOperation(
                    op="add",
                    path="/relations/-",
                    value={
                        "rel": "System.LinkTypes.Hierarchy-Reverse",
                        "url": f"{org_url}/{project}/_apis/wit/workItems/{epic_id}",
                    },
                )
            )

        work_item = wit_client.create_work_item(document=document, project=project, type="User Story")
        wi_id = str(work_item.id)
        logger.info("Created AzDO User Story: %s (ID: %s)", summary, wi_id)
        return f"Created User Story '{summary}' — Work Item ID: {wi_id}"

    except AzureDevOpsServiceError as e:
        logger.error("AzDO API error in azdevops_create_story: %s", e)
        return _azdo_error_msg(e)
    except Exception as e:
        logger.error("Unexpected error in azdevops_create_story: %s", e)
        return f"Error: {e}"


@tool
def azdevops_create_iteration(name: str, start_date: str = "", finish_date: str = "", project: str = "") -> str:
    """Create an iteration (sprint) in Azure DevOps. Only call after user confirms.

    Creates an iteration classification node with optional start and finish dates.
    start_date and finish_date are ISO date strings (e.g. "2026-03-16").
    Returns the iteration path on success.
    """
    project = project or get_azure_devops_project() or ""
    if not project:
        return "Error: No project specified. Set AZURE_DEVOPS_PROJECT in .env or pass project parameter."

    logger.debug("azdevops_create_iteration called: name=%r, project=%r", name, project)
    try:
        from yeaboi.azdevops_sync import _create_iteration_node

        org_url = get_azure_devops_org_url() or ""
        token = get_azure_devops_token() or ""
        if not org_url:
            return "Error: AZURE_DEVOPS_ORG_URL is not set."

        iteration_path = _create_iteration_node(org_url, token, project, name, start_date, finish_date)
        logger.info("Created AzDO Iteration: %s → %s", name, iteration_path)
        return f"Created Iteration '{name}' — Path: {iteration_path}"
    except Exception as e:
        logger.error("Unexpected error in azdevops_create_iteration: %s", e)
        return f"Error: {e}"


# ---------------------------------------------------------------------------
# Non-@tool helpers (used by azdevops_sync.py for batch operations)
# ---------------------------------------------------------------------------


def create_task(title: str, description: str, story_id: str, project: str = "") -> str:
    """Create a Task work item linked to a parent User Story.

    Not a @tool — called directly by azdevops_sync.py during batch sync.
    Returns the work item ID string.
    """
    project = project or get_azure_devops_project() or ""
    from azure.devops.v7_1.work_item_tracking.models import JsonPatchOperation

    wit_client = _make_azdo_clients()[0]
    org_url = get_azure_devops_org_url() or ""

    # Area path = "{project}\{team}" — assigns task to the team's board area.
    team = get_azure_devops_team() or ""
    area_path = f"{project}\\{team}" if team else project

    document = [
        JsonPatchOperation(op="add", path="/fields/System.Title", value=title),
        JsonPatchOperation(op="add", path="/fields/System.Description", value=description),
        JsonPatchOperation(op="add", path="/fields/System.AreaPath", value=area_path),
        JsonPatchOperation(
            op="add",
            path="/relations/-",
            value={
                "rel": "System.LinkTypes.Hierarchy-Reverse",
                "url": f"{org_url}/{project}/_apis/wit/workItems/{story_id}",
            },
        ),
    ]

    work_item = wit_client.create_work_item(document=document, project=project, type="Task")
    return str(work_item.id)


def add_work_items_to_iteration(work_item_ids: list[str], iteration_path: str, project: str = "") -> None:
    """Assign work items to an iteration by setting their System.IterationPath field.

    Not a @tool — called directly by azdevops_sync.py during batch sync.
    """
    project = project or get_azure_devops_project() or ""
    from azure.devops.v7_1.work_item_tracking.models import JsonPatchOperation

    wit_client = _make_azdo_clients()[0]

    for wi_id in work_item_ids:
        document = [
            JsonPatchOperation(op="add", path="/fields/System.IterationPath", value=iteration_path),
        ]
        wit_client.update_work_item(document=document, id=int(wi_id), project=project)


# ---------------------------------------------------------------------------
# Recent-activity helper for Daily Standup mode
# ---------------------------------------------------------------------------
# Plain function (not @tool) the standup collector calls directly. Returns
# structured data and degrades gracefully to [] on error/missing config.
# See docs: "Daily Standup" — recent-activity collection


def _identity_fields(raw) -> tuple[str, str]:
    """(displayName, email) from an AzDO identity value.

    Cloud returns an IdentityRef dict {displayName, uniqueName(email)}; some
    server versions return a plain "Name <email>" string — parse both shapes.
    """
    if not raw:
        return "", ""
    if isinstance(raw, dict):
        return raw.get("displayName", "") or "", raw.get("uniqueName", "") or ""
    text = str(raw)
    if "<" in text and text.rstrip().endswith(">"):
        name, _, rest = text.partition("<")
        return name.strip(), rest.rstrip(">").strip()
    return text.strip(), ""


def _work_item_url(project: str, wi_id: str) -> str:
    """Browser URL for a work item ("" when the org URL is unconfigured).

    Carried on activity items so standup surfaces can link the ticket.
    """
    base = (get_azure_devops_org_url() or "").rstrip("/")
    if not base or not wi_id:
        return ""
    from urllib.parse import quote

    return f"{base}/{quote(project)}/_workitems/edit/{wi_id}"


def azdevops_recent_activity(project: str = "", days: int = 1, since=None) -> list[dict]:
    """Return work items changed since the window start, plus in-progress (WIP) items.

    The window is ``since → now`` when ``since`` (a datetime — always a midnight
    for the standup) is given: WIQL's ``@Today - N`` is midnight-based, so the
    whole-day delta maps exactly. Else the last ``days`` days.

    Each changed item ({author, kind='work_item', title, status, timestamp,
    key(#id), author_email}) is credited to the person who actually made the
    change (System.ChangedBy), falling back to the assignee. WIP items
    (kind='wip') are assigned in-progress tickets untouched in the window —
    credited to their assignee — so quiet in-flight work stays visible.
    Returns [] when Azure DevOps is unconfigured or the WIQL query fails.
    """
    project = project or get_azure_devops_project() or ""
    logger.info("azdevops_recent_activity: project=%r days=%d since=%s", project, days, since)
    if not project:
        logger.warning("azdevops_recent_activity skipped — no project configured")
        return []
    try:
        from azure.devops.v7_1.work_item_tracking.models import Wiql

        wit_client, _ = _make_azdo_clients()
        # WIQL has no bind parameters: escape single quotes in `project` (config-derived)
        # and force the day delta to int so neither can alter the query. See
        # _normalize_work_item_state.
        safe_project = project.replace("'", "''")
        if since is not None:
            from datetime import date as _date

            days_back = max(0, (_date.today() - since.date()).days)
        else:
            days_back = int(days)
        wiql = Wiql(
            query=(
                "SELECT [System.Id] FROM WorkItems"  # noqa: S608 - read-only WIQL; inputs escaped/int-cast above
                f" WHERE [System.TeamProject] = '{safe_project}'"
                f" AND [System.ChangedDate] >= @Today - {days_back}"
                " ORDER BY [System.ChangedDate] DESC"
            )
        )
        fields = [
            "System.Id",
            "System.Title",
            "System.State",
            "System.AssignedTo",
            "System.ChangedBy",
            "System.ChangedDate",
        ]
        result = wit_client.query_by_wiql(wiql, top=100)
        items: list[dict] = []
        seen_ids: set[str] = set()
        if result.work_items:
            ids = [wi.id for wi in result.work_items]
            work_items = wit_client.get_work_items(ids, fields=fields)
            for item in work_items:
                f = item.fields
                assigned_name, assigned_email = _identity_fields(f.get("System.AssignedTo"))
                changed_name, changed_email = _identity_fields(f.get("System.ChangedBy"))
                # Credit the actual actor; the assignee is only a fallback.
                author, author_email = (
                    (changed_name, changed_email) if changed_name else (assigned_name, assigned_email)
                )
                wi_id = str(f.get("System.Id", ""))
                seen_ids.add(wi_id)
                items.append(
                    {
                        "author": author,
                        "author_email": author_email,
                        "kind": "work_item",
                        "title": f.get("System.Title", ""),
                        "status": f.get("System.State", ""),
                        "timestamp": str(f.get("System.ChangedDate", ""))[:19],
                        "key": f"#{wi_id}",
                        "url": _work_item_url(project, wi_id),
                    }
                )
        items.extend(_azdo_wip_items(wit_client, project, safe_project, seen_ids, fields))
        logger.info("azdevops_recent_activity: %d item(s) in last %d day(s)", len(items), days_back)
        return items
    except ValueError as e:
        logger.warning("azdevops_recent_activity skipped: %s", e)
        return []
    except AzureDevOpsServiceError as e:
        _raise_if_azdo_auth(e)
        logger.warning("azdevops_recent_activity failed: %s", _azdo_error_msg(e))
        return []
    except Exception as e:
        logger.warning("azdevops_recent_activity unexpected error: %s", e)
        return []


def azdevops_assignee_roster(project: str = "", days: int = 30) -> list[dict]:
    """Return every recent or active assignee with no activity-detail payload.

    WIQL discovers the complete ID set and work items are then fetched in
    bounded batches with only ``AssignedTo``. In particular, ``ChangedBy`` is
    not used as team-membership evidence.
    """
    project = project.strip() or (get_azure_devops_project() or "")
    if not project:
        raise ValueError("No Azure DevOps project configured")
    try:
        from azure.devops.v7_1.work_item_tracking.models import Wiql

        wit_client, _ = _make_azdo_clients()
        safe_project = project.replace("'", "''")
        wiql = Wiql(
            query=(
                "SELECT [System.Id] FROM WorkItems"  # noqa: S608 - escaped config + integer window
                f" WHERE [System.TeamProject] = '{safe_project}'"
                " AND [System.AssignedTo] <> ''"
                f" AND ([System.ChangedDate] >= @Today - {max(0, int(days))}"
                " OR [System.State] IN ('Active', 'In Progress', 'Doing', 'Committed'))"
                " ORDER BY [System.ChangedDate] DESC"
            )
        )
        result = wit_client.query_by_wiql(wiql)
        ids = [wi.id for wi in (getattr(result, "work_items", None) or [])]
        members: dict[str, dict] = {}
        batch_size = 200
        for offset in range(0, len(ids), batch_size):
            work_items = wit_client.get_work_items(
                ids[offset : offset + batch_size],
                fields=["System.Id", "System.AssignedTo"],
            )
            for item in work_items or []:
                raw = (getattr(item, "fields", None) or {}).get("System.AssignedTo")
                name, email = _identity_fields(raw)
                if not name:
                    continue
                if isinstance(raw, dict):
                    identity = raw.get("descriptor") or raw.get("id") or raw.get("uniqueName")
                else:
                    identity = ""
                identity = identity or email or name.casefold()
                members.setdefault(
                    str(identity),
                    {
                        "name": name,
                        "email": email,
                        "identity": str(identity),
                        "source": "azuredevops",
                    },
                )
        return list(members.values())
    except AzureDevOpsServiceError as exc:
        _raise_if_azdo_auth(exc)
        raise RuntimeError(_azdo_error_msg(exc)) from exc


def _azdo_wip_items(wit_client, project: str, safe_project: str, seen_ids: set[str], fields: list[str]) -> list[dict]:
    """Assigned in-progress work items — best-effort, degrades to [] on any failure."""
    try:
        from azure.devops.v7_1.work_item_tracking.models import Wiql

        wiql = Wiql(
            query=(
                "SELECT [System.Id] FROM WorkItems"  # noqa: S608 - read-only WIQL; project escaped by caller
                f" WHERE [System.TeamProject] = '{safe_project}'"
                " AND [System.State] IN ('Active', 'In Progress', 'Doing', 'Committed')"
                " AND [System.AssignedTo] <> ''"
                " ORDER BY [System.ChangedDate] DESC"
            )
        )
        result = wit_client.query_by_wiql(wiql, top=50)
        if not result.work_items:
            return []
        ids = [wi.id for wi in result.work_items]
        out: list[dict] = []
        for item in wit_client.get_work_items(ids, fields=fields):
            f = item.fields
            wi_id = str(f.get("System.Id", ""))
            if wi_id in seen_ids:
                continue  # already emitted with a fresher changed-in-window item
            assigned_name, assigned_email = _identity_fields(f.get("System.AssignedTo"))
            if not assigned_name:
                continue
            out.append(
                {
                    "author": assigned_name,
                    "author_email": assigned_email,
                    "kind": "wip",
                    "title": f.get("System.Title", ""),
                    "status": f.get("System.State", ""),
                    "timestamp": str(f.get("System.ChangedDate", ""))[:19],
                    "key": f"#{wi_id}",
                    "url": _work_item_url(project, wi_id),
                }
            )
        return out
    except Exception as e:  # WIP is a bonus signal — never let it break the main query's results
        logger.warning("azdevops wip query failed: %s", e)
        return []


# Caps for the repo-activity scan: bound the number of sequential API calls so
# a large org can't stall the standup (2 calls per repo).
_MAX_ACTIVITY_REPOS = 10
_MAX_REPO_COMMITS = 100
_MAX_REPO_PRS = 100
_MAX_CHANGED_FILE_LOOKUPS = 25
_MAX_REVIEW_THREAD_LOOKUPS = 25
_AZDO_REQUEST_TIMEOUT_SECONDS = 5


def _make_git_client(org_url: str | None = None, token: str | None = None):
    """Create an authenticated Git client — same connection pattern as _make_azdo_clients."""
    org_url = org_url or get_azure_devops_org_url()
    token = token or get_azure_devops_token()
    if not org_url:
        raise ValueError("AZURE_DEVOPS_ORG_URL is not set. Add it to your .env file.")
    client = _pin_client_base_url(_make_connection(org_url, token).clients.get_git_client(), org_url)
    # msrest defaults to 100 seconds per request. A dead DNS route or stale
    # repository must not hold a daily standup for several minutes.
    client.config.connection.timeout = _AZDO_REQUEST_TIMEOUT_SECONDS
    return client


def azdevops_analysis_inventory(
    projects: list[str] | tuple[str, ...],
    *,
    include_trees: bool = True,
) -> list[dict]:
    """Discover every repository in configured Azure DevOps projects."""
    out: list[dict] = []
    try:
        git_client = _make_git_client()
    except Exception as exc:
        return [
            {
                "provider": "azdo",
                "container": project,
                "name": project,
                "active": True,
                "paths": [],
                "error": f"repository discovery failed: {exc}",
                "discovery_error": True,
            }
            for project in projects
        ]
    for project in projects:
        try:
            repositories = git_client.get_repositories(project) or []
        except Exception as exc:
            out.append(
                {
                    "provider": "azdo",
                    "container": project,
                    "name": project,
                    "active": True,
                    "paths": [],
                    "error": f"repository discovery failed: {exc}",
                    "discovery_error": True,
                }
            )
            continue
        for repo in repositories:
            paths: list[str] = []
            tree_error = ""
            if include_trees:
                try:
                    tree = git_client.get_items(
                        repository_id=repo.id,
                        project=project,
                        scope_path="/",
                        recursion_level="Full",
                        include_content_metadata=True,
                    )
                    paths = [
                        str(getattr(item, "path", "") or "").lstrip("/")
                        for item in tree or []
                        if not bool(getattr(item, "is_folder", False))
                    ]
                except Exception as exc:
                    tree_error = str(exc)
            out.append(
                {
                    "provider": "azdo",
                    "container": project,
                    "name": str(getattr(repo, "name", "") or getattr(repo, "id", "")),
                    "repo_id": str(getattr(repo, "id", "") or ""),
                    "url": getattr(repo, "web_url", "") or "",
                    "default_branch": str(getattr(repo, "default_branch", "") or "").removeprefix("refs/heads/"),
                    "archived": bool(getattr(repo, "is_disabled", False)),
                    "active": True,
                    "paths": paths,
                    "error": tree_error,
                }
            )
    return out


def _repo_activity_cutoff(days: int, since):
    """Tz-aware UTC window start (since wins, else now − days)."""
    from datetime import UTC, datetime, timedelta

    if since is not None:
        return since.astimezone(UTC) if since.tzinfo else since.replace(tzinfo=UTC)
    return datetime.now(UTC) - timedelta(days=int(days))


def _aware(dt):
    """Coerce an SDK datetime to tz-aware UTC for safe comparison; None stays None."""
    from datetime import UTC

    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _activity_repo_web_url(repo, project: str) -> str:
    """Return a browser URL for an Azure repository reference.

    Project-wide PR endpoints commonly return a lightweight repository
    reference with ``id`` and ``name`` but no ``web_url``. Build the canonical
    browser URL from the configured organization instead of dropping the
    Standup evidence link or hydrating every repository with another request.
    """
    web_url = str(getattr(repo, "web_url", "") or "").rstrip("/")
    if web_url:
        return web_url
    org_url = str(get_azure_devops_org_url() or "").rstrip("/")
    repo_name = str(getattr(repo, "name", "") or "")
    if not org_url or not project or not repo_name:
        return ""
    return f"{org_url}/{quote(str(project), safe='')}/_git/{quote(repo_name, safe='')}"


def _activity_repositories(git_client, project: str, repositories: list[str] | None, metadata_cache=None):
    """Resolve explicit ``project/repo`` identifiers or the legacy project scan."""
    if repositories is None:

        def _discover():
            return [
                {
                    "id": getattr(repo, "id", ""),
                    "name": getattr(repo, "name", ""),
                    "web_url": getattr(repo, "web_url", ""),
                }
                for repo in (git_client.get_repositories(project) or [])
            ]

        rows = (
            metadata_cache.get_or_compute(
                "azure_devops",
                "repositories",
                project,
                "v1",
                _discover,
                ttl_seconds=600,
            )
            if metadata_cache is not None
            else _discover()
        )
        from types import SimpleNamespace

        return [(project, SimpleNamespace(**row)) for row in rows]
    resolved = []
    for spec in repositories:
        selected_project, sep, repo_name = str(spec).partition("/")
        if not sep or not selected_project or not repo_name:
            logger.warning("standup: invalid Azure repository selection %r", spec)
            continue
        try:
            resolved.append((selected_project, git_client.get_repository(repo_name, project=selected_project)))
        except Exception as exc:
            logger.warning("standup: Azure repository %s unavailable: %s", spec, exc)
    return resolved


def _activity_pull_requests(git_client, project: str, repositories: list[str] | None, criteria, metadata_cache=None):
    """Return ``(project, repository, PR)`` rows with one project-wide request when possible.

    Azure DevOps exposes a project-level pull-request endpoint. Using it avoids
    one list request for every repository in large projects and also avoids
    repeatedly touching stale repository IDs returned by discovery.
    """
    if repositories is None:

        def _list_project_prs():
            return list(git_client.get_pull_requests_by_project(project, criteria, top=_MAX_REPO_PRS) or [])

        prs = (
            metadata_cache.memoize(
                ("azure_devops", "project_pull_requests", project),
                _list_project_prs,
            )
            if metadata_cache is not None
            else _list_project_prs()
        )
        rows = []
        for pr in prs:
            repo = getattr(pr, "repository", None)
            if repo is None or not getattr(repo, "id", ""):
                logger.debug("azdevops: project PR %s has no repository metadata", getattr(pr, "pull_request_id", ""))
                continue
            rows.append((project, repo, pr))
        return rows

    rows = []
    for selected_project, repo in _activity_repositories(git_client, project, repositories, metadata_cache):
        try:

            def _list_repo_prs():
                return list(git_client.get_pull_requests(repo.id, criteria, project=selected_project, top=25) or [])

            prs = (
                metadata_cache.memoize(
                    ("azure_devops", "pull_requests", selected_project, str(repo.id)),
                    _list_repo_prs,
                )
                if metadata_cache is not None
                else _list_repo_prs()
            )
        except Exception as exc:
            logger.warning("azdevops: repo %s PR listing failed: %s", getattr(repo, "name", "?"), exc)
            continue
        rows.extend((selected_project, repo, pr) for pr in prs)
    return rows


def _azdo_commit_changed_files(
    git_client,
    *,
    project: str,
    repository_id: str,
    commit_id: str,
    metadata_cache=None,
) -> list[str]:
    """Best-effort changed paths for one Azure Repos commit."""
    if not commit_id:
        return []

    def _fetch() -> list[str]:
        try:
            with _AZDO_DETAIL_SEMAPHORE:
                changes = git_client.get_changes(
                    commit_id=commit_id,
                    repository_id=repository_id,
                    project=project,
                    top=100,
                )
            return [
                str(getattr(getattr(change, "item", None), "path", "") or "")
                for change in list(getattr(changes, "changes", changes) or ())[:100]
                if getattr(getattr(change, "item", None), "path", "")
            ]
        except Exception as exc:
            logger.debug("azdevops commit changed-file lookup failed for %s: %s", commit_id[:8], exc)
            return []

    if metadata_cache is not None:
        return list(
            metadata_cache.get_or_compute(
                "azure_devops",
                "changed_files",
                f"{project}:{repository_id}:{commit_id}",
                commit_id,
                _fetch,
                cache_empty=False,
            )
        )
    return _fetch()


def _azdo_pr_changed_files(
    git_client,
    *,
    project: str,
    repository_id: str,
    pr_id,
    metadata_cache=None,
) -> list[str]:
    """Best-effort changed paths for the newest iteration of an Azure Repos PR."""
    try:
        with _AZDO_DETAIL_SEMAPHORE:
            iterations = list(git_client.get_pull_request_iterations(repository_id, pr_id, project=project) or ())
        if not iterations:
            return []
        iteration_id = getattr(iterations[-1], "id", None)

        def _fetch() -> list[str]:
            with _AZDO_DETAIL_SEMAPHORE:
                changes = git_client.get_pull_request_iteration_changes(
                    repository_id,
                    pr_id,
                    iteration_id,
                    project=project,
                    top=100,
                )
            entries = getattr(changes, "change_entries", ()) or ()
            return [
                str(getattr(getattr(change, "item", None), "path", "") or "")
                for change in list(entries)[:100]
                if getattr(getattr(change, "item", None), "path", "")
            ]

        if metadata_cache is not None:
            return list(
                metadata_cache.get_or_compute(
                    "azure_devops",
                    "pr_changed_files",
                    f"{project}:{repository_id}:{pr_id}",
                    str(iteration_id),
                    _fetch,
                    cache_empty=False,
                    replace_revisions=True,
                )
            )
        return _fetch()
    except Exception as exc:
        logger.debug("azdevops PR changed-file lookup failed for %s: %s", pr_id, exc)
        return []


def _analysis_repository(value):
    """Normalize an inventory dictionary into the SDK-like shape collectors use."""
    if not isinstance(value, dict):
        return value
    return SimpleNamespace(
        id=value.get("repo_id") or value.get("name", ""),
        name=value.get("name", ""),
        web_url=value.get("url", ""),
    )


# Per-repo bound on full-message refetches for truncated commit comments.
# Truncation only hits long messages, so this is rarely approached; the cap
# keeps a pathological repo (huge generated messages) from doubling its calls.
_TRUNCATED_COMMENT_REFETCH_CAP = 25


def _full_commit_comment(git_client, project: str, repository_id, commit) -> str:
    """Full commit message, refetching when the batch API truncated it.

    ``get_commits`` truncates long ``comment`` values (``comment_truncated``)
    and there is no criteria flag for full messages — but trailers like
    Co-Authored-By live at the END of the message, exactly what truncation
    strips, so AI markers would silently vanish. Best-effort: any refetch
    error logs a warning and falls back to the truncated text.
    """
    comment = getattr(commit, "comment", "") or ""
    if not getattr(commit, "comment_truncated", False):
        return comment
    try:
        full = git_client.get_commit(getattr(commit, "commit_id", "") or "", repository_id, project=project)
        return getattr(full, "comment", "") or comment
    except Exception as e:
        logger.warning("azdevops: full-comment refetch failed for %.8s: %s", getattr(commit, "commit_id", ""), e)
        return comment


def azdevops_recent_commits(
    project: str = "",
    days: int = 1,
    since=None,
    *,
    include_repository: bool = False,
    repositories: list[dict] | list[str] | None = None,
    progress_callback=None,
    metadata_cache=None,
) -> list[dict]:
    """Return commits pushed to the project's repos since the window start.

    Scans every repository in the project (all branches are NOT walked — the
    commit search covers the default branch per
    repo, which is where merged work lands). Each item: {author, author_email,
    kind='commit', title(first line + repo name), body, timestamp, key(sha[:8])}.
    ``body`` is the commit message body (Co-Authored-By / AI-tool trailers).
    Returns [] when Azure DevOps is unconfigured or the API fails.
    """
    project = project or get_azure_devops_project() or ""
    logger.info("azdevops_recent_commits: project=%r days=%d since=%s", project, days, since)
    if not project and not repositories:
        return []
    try:
        from azure.devops.v7_1.git.models import GitQueryCommitsCriteria

        discovery_client = _make_git_client() if repositories is None else None
        cutoff = _repo_activity_cutoff(days, since)
        criteria = GitQueryCommitsCriteria(from_date=cutoff.strftime("%Y-%m-%dT%H:%M:%SZ"))
        if repositories and isinstance(repositories[0], str):
            selector_client = _make_git_client()
            repo_list = _activity_repositories(selector_client, project, repositories, metadata_cache)
        else:
            repo_list = [
                (project, _analysis_repository(repo))
                for repo in (
                    repositories if repositories is not None else discovery_client.get_repositories(project) or []
                )
                if not isinstance(repo, dict) or not repo.get("discovery_error")
            ]

        def _read_repo(selected_project, repo) -> list[dict]:
            git_client = _make_git_client()
            try:
                commits: list = []
                skip = 0
                seen_commit_ids: set[str] = set()
                while True:
                    try:
                        chunk = (
                            git_client.get_commits(
                                repository_id=repo.id,
                                search_criteria=criteria,
                                project=selected_project,
                                skip=skip,
                                top=100,
                            )
                            or []
                        )
                    except TypeError:
                        chunk = (
                            git_client.get_commits(
                                repository_id=repo.id,
                                search_criteria=criteria,
                                project=selected_project,
                            )
                            or []
                        )
                    new_chunk = [
                        commit
                        for commit in chunk
                        if str(getattr(commit, "commit_id", "") or id(commit)) not in seen_commit_ids
                    ]
                    for commit in new_chunk:
                        seen_commit_ids.add(str(getattr(commit, "commit_id", "") or id(commit)))
                    commits.extend(new_chunk)
                    if chunk and not new_chunk:
                        break
                    # Standup path: bounded feed — the exhaustive analysis path
                    # (include_repository=True) walks the whole window instead.
                    if not include_repository and len(commits) >= _MAX_REPO_COMMITS:
                        logger.info(
                            "azdevops_recent_commits: capped at %d commits for %s",
                            _MAX_REPO_COMMITS,
                            getattr(repo, "name", "?"),
                        )
                        commits = commits[:_MAX_REPO_COMMITS]
                        break
                    if len(chunk) < 100:
                        break
                    skip += len(chunk)
            except Exception as e:  # one bad/empty repo must not hide the others
                logger.warning("azdevops_recent_commits: repo %s failed: %s", getattr(repo, "name", "?"), e)
                return []
            repo_web = _activity_repo_web_url(repo, selected_project)
            repo_items: list[dict] = []
            refetched = 0
            file_lookups = 0
            for commit in commits or []:
                author = getattr(commit, "author", None)
                if getattr(commit, "comment_truncated", False) and refetched < _TRUNCATED_COMMENT_REFETCH_CAP:
                    comment = _full_commit_comment(git_client, selected_project, repo.id, commit)
                    refetched += 1
                else:
                    comment = getattr(commit, "comment", "") or ""
                message = comment.splitlines()
                body = "\n".join(message[1:]).strip()  # Co-Authored-By / AI-tool trailers live here
                sha = getattr(commit, "commit_id", "") or ""
                # Standup path only: per-commit change lookups are capped per repo
                # so a long window can't turn into an API call per commit. The
                # analysis path fetches change metadata separately (pooled).
                changed_files: list[dict] = []
                if not include_repository and file_lookups < _MAX_CHANGED_FILE_LOOKUPS:
                    file_lookups += 1
                    changed_files = _azdo_commit_changed_files(
                        git_client,
                        project=selected_project,
                        repository_id=repo.id,
                        commit_id=sha,
                        metadata_cache=metadata_cache,
                    )
                item = {
                    "author": getattr(author, "name", "") or "",
                    "author_email": getattr(author, "email", "") or "",
                    "kind": "commit",
                    "title": f"{message[0] if message else ''} ({repo.name})",
                    "body": body,
                    "timestamp": str(getattr(author, "date", "") or "")[:19],
                    "key": sha[:8],
                    "commit_id": sha,
                    "url": f"{repo_web}/commit/{sha}" if repo_web and sha else "",
                    "changed_files": changed_files,
                }
                if include_repository:
                    item["repository"] = repo.name
                else:
                    item["repository"] = f"{selected_project}/{repo.name}"
                repo_items.append(item)
            if refetched >= _TRUNCATED_COMMENT_REFETCH_CAP:
                logger.info(
                    "azdevops_recent_commits: repo %s hit the truncated-comment refetch cap (%d)",
                    getattr(repo, "name", "?"),
                    _TRUNCATED_COMMENT_REFETCH_CAP,
                )
            return repo_items

        from yeaboi.config import get_team_analysis_code_max_concurrency

        results: dict[int, list[dict]] = {}
        if repo_list:
            with ThreadPoolExecutor(
                max_workers=min(get_team_analysis_code_max_concurrency(), len(repo_list)),
                thread_name_prefix="azdo-commits",
            ) as executor:
                futures = {
                    executor.submit(_read_repo, selected_project, repo): index
                    for index, (selected_project, repo) in enumerate(repo_list)
                }
                completed = 0
                for future in as_completed(futures):
                    results[futures[future]] = future.result()
                    completed += 1
                    if progress_callback:
                        progress_callback(completed, len(repo_list))
        items = [item for index in range(len(repo_list)) for item in results.get(index, [])]
        logger.info("azdevops_recent_commits: %d commit(s)", len(items))
        return items
    except ValueError as e:
        logger.warning("azdevops_recent_commits skipped: %s", e)
        return []
    except AzureDevOpsServiceError as e:
        _raise_if_azdo_auth(e)
        logger.warning("azdevops_recent_commits failed: %s", _azdo_error_msg(e))
        return []
    except Exception as e:
        logger.warning("azdevops_recent_commits unexpected error: %s", e)
        return []


def azdevops_recent_prs(
    project: str = "",
    days: int = 1,
    since=None,
    *,
    include_repository: bool = False,
    repositories: list[dict] | list[str] | None = None,
    progress_callback=None,
    metadata_cache=None,
) -> list[dict]:
    """Return pull requests created or closed in the project's repos since the window start.

    The v7_1 PR search criteria has no time filters, so PRs are fetched
    newest-first per repo (top 25) and filtered client-side by creation/closed
    date. Each item: {author, author_email, kind='pr', title(+repo name), body,
    branch, status, timestamp, key(!id)} (``body`` is the PR description,
    ``branch`` the source branch without the refs/heads/ prefix). Returns [] on
    missing config or API failure.
    """
    project = project or get_azure_devops_project() or ""
    logger.info("azdevops_recent_prs: project=%r days=%d since=%s", project, days, since)
    if not project and not repositories:
        return []
    try:
        from azure.devops.v7_1.git.models import GitPullRequestSearchCriteria

        discovery_client = _make_git_client() if repositories is None else None
        cutoff = _repo_activity_cutoff(days, since)
        criteria = GitPullRequestSearchCriteria(status="all")
        if not include_repository:
            git_client = _make_git_client()
            items: list[dict] = []
            file_lookups = 0
            for selected_project, repo, pr in _activity_pull_requests(
                git_client,
                project,
                repositories,
                criteria,
                metadata_cache,
            ):
                created = _aware(getattr(pr, "creation_date", None))
                closed = _aware(getattr(pr, "closed_date", None))
                if not ((created and created >= cutoff) or (closed and closed >= cutoff)):
                    continue
                creator = getattr(pr, "created_by", None)
                status = getattr(pr, "status", "") or ""
                pr_id = getattr(pr, "pull_request_id", "")
                repo_web = _activity_repo_web_url(repo, selected_project)
                # Standup path: per-PR change lookups capped so a busy window
                # can't turn into an API call per PR.
                changed_files: list[dict] = []
                if file_lookups < _MAX_CHANGED_FILE_LOOKUPS:
                    file_lookups += 1
                    changed_files = _azdo_pr_changed_files(
                        git_client,
                        project=selected_project,
                        repository_id=repo.id,
                        pr_id=pr_id,
                        metadata_cache=metadata_cache,
                    )
                items.append(
                    {
                        "author": getattr(creator, "display_name", "") or "",
                        "author_email": getattr(creator, "unique_name", "") or "",
                        "kind": "pr",
                        "title": f"{getattr(pr, 'title', '') or ''} ({repo.name})",
                        "body": getattr(pr, "description", "") or "",
                        # Source branch — agent-created PRs ("codex/…") carry
                        # their strongest AI marker here.
                        "branch": (getattr(pr, "source_ref_name", "") or "").removeprefix("refs/heads/"),
                        "status": "merged" if status == "completed" else status,
                        "timestamp": str(closed or created or "")[:19],
                        "key": f"!{pr_id}",
                        "pr_id": pr_id,
                        "url": f"{repo_web}/pullrequest/{pr_id}" if repo_web and pr_id else "",
                        "repository": f"{selected_project}/{repo.name}",
                        "changed_files": changed_files,
                    }
                )
            logger.info("azdevops_recent_prs: %d PR(s)", len(items))
            return items

        if repositories and isinstance(repositories[0], str):
            selector_client = _make_git_client()
            repo_list = _activity_repositories(selector_client, project, repositories, metadata_cache)
        else:
            repo_list = [
                (project, _analysis_repository(repo))
                for repo in (
                    repositories if repositories is not None else discovery_client.get_repositories(project) or []
                )
                if not isinstance(repo, dict) or not repo.get("discovery_error")
            ]

        def _read_repo(selected_project, repo) -> list[dict]:
            git_client = _make_git_client()
            try:
                prs: list = []
                skip = 0
                seen_pr_ids: set[str] = set()
                while True:
                    try:
                        chunk = (
                            git_client.get_pull_requests(
                                repo.id,
                                criteria,
                                project=selected_project,
                                skip=skip,
                                top=100,
                            )
                            or []
                        )
                    except TypeError:
                        chunk = (
                            git_client.get_pull_requests(
                                repo.id,
                                criteria,
                                project=selected_project,
                                top=100,
                            )
                            or []
                        )
                    new_chunk = [
                        pr for pr in chunk if str(getattr(pr, "pull_request_id", "") or id(pr)) not in seen_pr_ids
                    ]
                    for pr in new_chunk:
                        seen_pr_ids.add(str(getattr(pr, "pull_request_id", "") or id(pr)))
                    prs.extend(new_chunk)
                    if chunk and not new_chunk:
                        break
                    if len(chunk) < 100:
                        break
                    skip += len(chunk)
            except Exception as e:
                logger.warning("azdevops_recent_prs: repo %s failed: %s", getattr(repo, "name", "?"), e)
                return []
            repo_items: list[dict] = []
            for pr in prs or []:
                created = _aware(getattr(pr, "creation_date", None))
                closed = _aware(getattr(pr, "closed_date", None))
                if not ((created and created >= cutoff) or (closed and closed >= cutoff)):
                    continue
                creator = getattr(pr, "created_by", None)
                status = getattr(pr, "status", "") or ""
                pr_id = getattr(pr, "pull_request_id", "")
                repo_web = _activity_repo_web_url(repo, selected_project)
                item = {
                    "author": getattr(creator, "display_name", "") or "",
                    "author_email": getattr(creator, "unique_name", "") or "",
                    "kind": "pr",
                    "title": f"{getattr(pr, 'title', '') or ''} ({repo.name})",
                    "body": getattr(pr, "description", "") or "",  # PR description
                    # Source branch — agent-created PRs ("codex/…") carry
                    # their strongest AI marker here.
                    "branch": (getattr(pr, "source_ref_name", "") or "").removeprefix("refs/heads/"),
                    "status": "merged" if status == "completed" else status,
                    "timestamp": str(closed or created or "")[:19],
                    "key": f"!{pr_id}",
                    "pr_id": pr_id,
                    "url": f"{repo_web}/pullrequest/{pr_id}" if repo_web and pr_id else "",
                    "changed_files": (
                        _azdo_pr_changed_files(
                            git_client,
                            project=selected_project,
                            repository_id=repo.id,
                            pr_id=pr_id,
                            metadata_cache=metadata_cache,
                        )
                        if metadata_cache is not None
                        else []
                    ),
                }
                if include_repository:
                    item["repository"] = repo.name
                else:
                    item["repository"] = f"{selected_project}/{repo.name}"
                repo_items.append(item)
                for reviewer in getattr(pr, "reviewers", ()) or ():
                    vote = int(getattr(reviewer, "vote", 0) or 0)
                    if vote == 0:
                        continue
                    review_item = {
                        "author": getattr(reviewer, "display_name", "") or "",
                        "author_email": getattr(reviewer, "unique_name", "") or "",
                        "kind": "review",
                        "title": f"Reviewed PR !{pr_id}: {getattr(pr, 'title', '') or ''}",
                        "body": "",
                        "status": str(vote),
                        "timestamp": str(closed or created or "")[:19],
                        "key": f"review:{pr_id}:{getattr(reviewer, 'id', '')}",
                        "pr_id": pr_id,
                        "url": f"{repo_web}/pullrequest/{pr_id}" if repo_web and pr_id else "",
                    }
                    if include_repository:
                        review_item["repository"] = repo.name
                    else:
                        review_item["repository"] = f"{selected_project}/{repo.name}"
                    repo_items.append(review_item)
            return repo_items

        from yeaboi.config import get_team_analysis_code_max_concurrency

        results: dict[int, list[dict]] = {}
        if repo_list:
            with ThreadPoolExecutor(
                max_workers=min(get_team_analysis_code_max_concurrency(), len(repo_list)),
                thread_name_prefix="azdo-prs",
            ) as executor:
                futures = {
                    executor.submit(_read_repo, selected_project, repo): index
                    for index, (selected_project, repo) in enumerate(repo_list)
                }
                completed = 0
                for future in as_completed(futures):
                    results[futures[future]] = future.result()
                    completed += 1
                    if progress_callback:
                        progress_callback(completed, len(repo_list))
        items = [item for index in range(len(repo_list)) for item in results.get(index, [])]
        logger.info("azdevops_recent_prs: %d PR(s)", len(items))
        return items
    except ValueError as e:
        logger.warning("azdevops_recent_prs skipped: %s", e)
        return []
    except AzureDevOpsServiceError as e:
        _raise_if_azdo_auth(e)
        logger.warning("azdevops_recent_prs failed: %s", _azdo_error_msg(e))
        return []
    except Exception as e:
        logger.warning("azdevops_recent_prs unexpected error: %s", e)
        return []


def azdevops_recent_reviews(
    project: str = "", days: int = 1, since=None, repositories: list[str] | None = None, metadata_cache=None
) -> list[dict]:
    """Return timestamped Azure Repos PR review comments for selected repositories.

    Azure reviewer votes do not expose a reliable event timestamp through this
    API, so approvals are reported only when represented by a timestamped
    thread/comment. This avoids assigning old approvals to today's standup.
    """
    project = project or get_azure_devops_project() or ""
    if not project and not repositories:
        return []
    try:
        from azure.devops.v7_1.git.models import GitPullRequestSearchCriteria

        git_client = _make_git_client()
        cutoff = _repo_activity_cutoff(days, since)
        criteria = GitPullRequestSearchCriteria(status="all")
        items: list[dict] = []
        rows = _activity_pull_requests(git_client, project, repositories, criteria, metadata_cache)
        # Old completed PRs cannot acquire new review comments. Keep recently
        # created/closed PRs plus active PRs, then bound the expensive thread
        # lookups across the whole project rather than 25 per repository.
        eligible_rows = []
        for selected_project, repo, pr in rows:
            created = _aware(getattr(pr, "creation_date", None))
            closed = _aware(getattr(pr, "closed_date", None))
            status = str(getattr(pr, "status", "") or "").lower()
            if status == "active" or (created and created >= cutoff) or (closed and closed >= cutoff):
                eligible_rows.append((selected_project, repo, pr))
        eligible_rows = eligible_rows[:_MAX_REVIEW_THREAD_LOOKUPS]

        def _reviews_for_pr(index_row) -> list[dict]:
            index, (selected_project, repo, pr) = index_row
            out: list[dict] = []
            pr_id = getattr(pr, "pull_request_id", "")
            changed_files = (
                _azdo_pr_changed_files(
                    git_client,
                    project=selected_project,
                    repository_id=repo.id,
                    pr_id=pr_id,
                    metadata_cache=metadata_cache,
                )
                if index < _MAX_CHANGED_FILE_LOOKUPS
                else []
            )
            try:
                with _AZDO_DETAIL_SEMAPHORE:
                    threads = git_client.get_threads(repo.id, pr_id, project=selected_project) or []
            except Exception as exc:
                logger.warning("azdevops_recent_reviews: PR %s threads failed: %s", pr_id, exc)
                return []
            for thread in threads:
                for comment in getattr(thread, "comments", ()) or ():
                    published = _aware(getattr(comment, "published_date", None))
                    if published is None or published < cutoff:
                        continue
                    author = getattr(comment, "author", None)
                    comment_id = getattr(comment, "id", "")
                    repo_web = _activity_repo_web_url(repo, selected_project)
                    out.append(
                        {
                            "author": getattr(author, "display_name", "") or "",
                            "author_email": getattr(author, "unique_name", "") or "",
                            "kind": "review",
                            "title": f"reviewed PR !{pr_id}: {getattr(pr, 'title', '') or ''} ({repo.name})",
                            "body": getattr(comment, "content", "") or "",
                            "status": "commented",
                            "timestamp": str(published)[:19],
                            "key": f"review-comment-{comment_id}",
                            "url": f"{repo_web}/pullrequest/{pr_id}" if repo_web and pr_id else "",
                            "repository": f"{selected_project}/{repo.name}",
                            "changed_files": changed_files,
                        }
                    )
            return out

        with ThreadPoolExecutor(
            max_workers=min(6, max(1, len(eligible_rows))),
            thread_name_prefix="standup-azdo-reviews",
        ) as pool:
            items.extend(item for batch in pool.map(_reviews_for_pr, enumerate(eligible_rows)) for item in batch)
        logger.info("azdevops_recent_reviews: %d review event(s)", len(items))
        return items
    except ValueError as exc:
        logger.warning("azdevops_recent_reviews skipped: %s", exc)
        return []
    except AzureDevOpsServiceError as exc:
        _raise_if_azdo_auth(exc)
        logger.warning("azdevops_recent_reviews failed: %s", _azdo_error_msg(exc))
        return []
    except Exception as exc:
        logger.warning("azdevops_recent_reviews unexpected error: %s", exc)
        return []


def azdevops_list_projects() -> list[str]:
    """Return every accessible, well-formed project in the configured organisation."""
    connection = _make_connection(get_azure_devops_org_url(), get_azure_devops_token())
    client = _pin_client_base_url(connection.clients.get_core_client(), get_azure_devops_org_url())
    out: list[str] = []
    skip = 0
    while True:
        page = client.get_projects(state_filter="wellFormed", top=100, skip=skip) or []
        for project in page:
            name = str(getattr(project, "name", "") or "").strip()
            if name and name not in out:
                out.append(name)
        if len(page) < 100:
            break
        skip += len(page)
    return sorted(out, key=str.lower)


def _azdo_change_page_items(page, attribute: str) -> list:
    """Normalize Azure SDK page wrappers and legacy list responses."""
    if page is None:
        return []
    if isinstance(page, (list, tuple)):
        return list(page)
    items = getattr(page, attribute, None)
    if items is None and isinstance(page, dict):
        items = page.get(attribute)
        if items is None:
            wire_name = "changeEntries" if attribute == "change_entries" else attribute
            items = page.get(wire_name)
    return list(items or [])


def _azdo_value(value, name: str, default=""):
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def azdevops_changed_files(project: str, repository: str, activity: list[dict]) -> list[dict]:
    """Fetch files changed by already member-scoped commits and authored PRs."""
    git_client = _make_git_client()
    out: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for item in activity:
        try:
            if item.get("kind") == "commit" and item.get("commit_id"):
                origin = str(item["commit_id"])
                changes = []
                skip = 0
                while True:
                    page = git_client.get_changes(
                        commit_id=origin,
                        repository_id=repository,
                        project=project,
                        top=2000,
                        skip=skip,
                    )
                    page_items = _azdo_change_page_items(page, "changes")
                    changes.extend(page_items)
                    if len(page_items) < 2000:
                        break
                    skip += len(page_items)
                attribution = "authored_commit"
                confidence = "high"
            elif item.get("kind") == "pr" and item.get("pr_id"):
                pr_id = int(item["pr_id"])
                iterations = (
                    git_client.get_pull_request_iterations(
                        repository_id=repository,
                        pull_request_id=pr_id,
                        project=project,
                    )
                    or []
                )
                if not iterations:
                    changes = []
                else:
                    iteration_id = int(getattr(iterations[-1], "id", 0) or 0)
                    changes = []
                    skip = 0
                    while True:
                        page = git_client.get_pull_request_iteration_changes(
                            repository_id=repository,
                            pull_request_id=pr_id,
                            iteration_id=iteration_id,
                            project=project,
                            top=2000,
                            skip=skip,
                        )
                        page_items = _azdo_change_page_items(page, "change_entries")
                        changes.extend(page_items)
                        next_skip = _azdo_value(page, "next_skip", None)
                        next_top = _azdo_value(page, "next_top", None)
                        if next_skip is not None and int(next_skip) > skip:
                            skip = int(next_skip)
                            if not next_top:
                                break
                            continue
                        if len(page_items) < 2000:
                            break
                        skip += len(page_items)
                origin = f"pr:{pr_id}"
                attribution = "authored_pr"
                confidence = "medium"
            else:
                continue
            for change in changes:
                changed_item = _azdo_value(change, "item", None)
                path = str(_azdo_value(changed_item, "path", "") or "").lstrip("/")
                dedupe = (origin, path, attribution)
                if not path or dedupe in seen:
                    continue
                seen.add(dedupe)
                change_type = str(
                    _azdo_value(change, "change_type", _azdo_value(change, "changeType", "")) or "edit"
                ).lower()
                out.append(
                    {
                        "provider": "azdo",
                        "container": project,
                        "repository": repository,
                        "path": path,
                        "status": change_type,
                        "additions": 0,
                        "deletions": 0,
                        "patch": "",
                        "truncated": False,
                        "author": item.get("author", ""),
                        "author_email": item.get("author_email", ""),
                        "attribution": attribution,
                        "confidence": confidence,
                        "change_id": origin,
                        "url": item.get("url", ""),
                        "error": "",
                    }
                )
        except Exception as exc:
            out.append(
                {
                    "provider": "azdo",
                    "container": project,
                    "repository": repository,
                    "path": str(item.get("key", "unknown change")),
                    "status": "failed",
                    "author": item.get("author", ""),
                    "attribution": "authored_commit" if item.get("kind") == "commit" else "authored_pr",
                    "confidence": "high" if item.get("kind") == "commit" else "medium",
                    "change_id": str(item.get("commit_id") or f"pr:{item.get('pr_id', '')}"),
                    "url": item.get("url", ""),
                    "error": str(exc),
                }
            )
    return out


def azdevops_active_sprint_progress(project: str = "") -> dict:
    """Return live progress for the active iteration: start date + burn-down points.

    Returns {sprint_name, start_date, completed_points, committed_points}; omits
    missing pieces; returns {} when unconfigured or on failure. Used by the
    standup engine. Reuses the Microsoft.VSTS.Scheduling.StoryPoints field like
    azdevops_fetch_velocity.
    """
    project = project or get_azure_devops_project() or ""
    logger.info("azdevops_active_sprint_progress: project=%r", project)
    if not project:
        return {}
    try:
        from datetime import datetime as _dt

        from azure.devops.v7_1.work.models import TeamContext

        wit_client, work_client = _make_azdo_clients()
        team = get_azure_devops_team() or f"{project} Team"
        team_context = TeamContext(project=project, team=team)

        all_iterations = work_client.get_team_iterations(team_context) or []
        now = _dt.now(UTC)
        current = [
            it
            for it in all_iterations
            if getattr(getattr(it, "attributes", None), "start_date", None)
            and getattr(it.attributes, "finish_date", None)
            and it.attributes.start_date <= now <= it.attributes.finish_date
        ]
        if not current:
            return {}
        cur = current[0]
        out: dict = {"sprint_name": cur.name}
        start = getattr(cur.attributes, "start_date", None)
        if start:
            out["start_date"] = start.strftime("%Y-%m-%d")

        work_items = work_client.get_iteration_work_items(team_context, cur.id)
        wi_ids = [
            rel.target.id
            for rel in getattr(work_items, "work_item_relations", []) or []
            if getattr(rel, "target", None)
        ]
        committed = 0.0
        completed = 0.0
        if wi_ids:
            items = wit_client.get_work_items(wi_ids, fields=["System.State", "Microsoft.VSTS.Scheduling.StoryPoints"])
            for item in items or []:
                pts = item.fields.get("Microsoft.VSTS.Scheduling.StoryPoints")
                try:
                    pts = float(pts) if pts else 0.0
                except (TypeError, ValueError):
                    pts = 0.0
                committed += pts
                if item.fields.get("System.State", "") in ("Closed", "Done", "Resolved", "Completed"):
                    completed += pts
        out["completed_points"] = completed
        out["committed_points"] = committed
        logger.info(
            "azdevops_active_sprint_progress: sprint=%r completed=%.1f committed=%.1f",
            cur.name,
            completed,
            committed,
        )
        return out
    except ValueError as e:
        logger.warning("azdevops_active_sprint_progress skipped: %s", e)
        return {}
    except AzureDevOpsServiceError as e:
        _raise_if_azdo_auth(e)
        logger.warning("azdevops_active_sprint_progress failed: %s", _azdo_error_msg(e))
        return {}
    except Exception as e:
        logger.warning("azdevops_active_sprint_progress unexpected error: %s", e)
        return {}


def azdevops_list_sprints(project: str = "", limit: int = 30) -> list[dict]:
    """Return the team's iterations (sprints) with date ranges.

    Each item: {id, path, name, start_date (YYYY-MM-DD), end_date (YYYY-MM-DD), state}.
    Reuses the same team-iteration read as azdevops_active_sprint_progress. Returns []
    when unconfigured or on failure. Used by Reporting mode's quarter view to let the
    user pick which sprints make up the quarter, and by Poker mode's sprint picker
    (which needs the iteration id to fetch the iteration's work items).
    """
    project = project or get_azure_devops_project() or ""
    logger.info("azdevops_list_sprints: project=%r limit=%d", project, limit)
    if not project:
        return []
    try:
        from datetime import datetime as _dt

        from azure.devops.v7_1.work.models import TeamContext

        _wit_client, work_client = _make_azdo_clients()
        team = get_azure_devops_team() or f"{project} Team"
        team_context = TeamContext(project=project, team=team)

        all_iterations = work_client.get_team_iterations(team_context) or []
        now = _dt.now(UTC)
        out: list[dict] = []
        for it in all_iterations:
            attrs = getattr(it, "attributes", None)
            start = getattr(attrs, "start_date", None)
            finish = getattr(attrs, "finish_date", None)
            if not (start and finish):
                continue
            if start <= now <= finish:
                state = "active"
            elif finish < now:
                state = "closed"
            else:
                state = "future"
            out.append(
                {
                    "id": getattr(it, "id", "") or "",
                    "path": getattr(it, "path", "") or "",
                    "name": getattr(it, "name", "") or "",
                    "start_date": start.strftime("%Y-%m-%d"),
                    "end_date": finish.strftime("%Y-%m-%d"),
                    "state": state,
                }
            )
        out.sort(key=lambda s: s["start_date"] or "0000-00-00")
        logger.info("azdevops_list_sprints: %d iteration(s)", len(out))
        return out[-limit:] if limit and len(out) > limit else out
    except ValueError as e:
        logger.warning("azdevops_list_sprints skipped: %s", e)
        return []
    except AzureDevOpsServiceError as e:
        _raise_if_azdo_auth(e)
        logger.warning("azdevops_list_sprints failed: %s", _azdo_error_msg(e))
        return []
    except Exception as e:
        logger.warning("azdevops_list_sprints unexpected error: %s", e)
        return []


# ---------------------------------------------------------------------------
# Per-work-item fetch + field update helpers for Poker mode
# ---------------------------------------------------------------------------
# Plain functions (not @tool) called directly by poker/tickets.py. They return
# structured rows / result tuples and degrade gracefully: a live poker session
# must never crash because the tracker is unavailable.
# See docs: "Tools" — tool types, read-only vs write tools

# Fields requested for every poker ticket row. StoryPoints is the same field
# used by azdevops_fetch_velocity / azdevops_active_sprint_progress.
# WorkItemType feeds the category filter; AcceptanceCriteria is AzDO's builtin
# AC field (Agile/Scrum templates; absent fields just come back empty).
_POKER_WI_FIELDS = [
    "System.Id",
    "System.Title",
    "System.Description",
    "System.State",
    "System.AssignedTo",
    "System.WorkItemType",
    "Microsoft.VSTS.Scheduling.StoryPoints",
    "Microsoft.VSTS.Common.AcceptanceCriteria",
]

# get_work_items caps a batch at 200 ids — page larger iterations.
_WI_BATCH = 200

# Work-item types offered for estimation; states that mean "already done".
_POKER_BACKLOG_TYPES = ("User Story", "Product Backlog Item", "Bug")
_POKER_CLOSED_STATES = ("Done", "Closed", "Removed", "Completed", "Resolved")

# Canonical poker type categories -> AzDO work-item type names. Unknown names
# (custom process types) are always KEPT by the filter — narrowing must never
# empty the fetch on an exotic process template. AzDO "Task" is a child work
# item (the sub-task analog), which is why it's a category of its own.
_POKER_TYPE_NAMES: dict[str, tuple[str, ...]] = {
    "story": ("User Story", "Product Backlog Item"),
    "bug": ("Bug",),
    "task": ("Task",),
}


def _work_item_type_allowed(type_name: str, include_types: tuple[str, ...]) -> bool:
    """Category filter on System.WorkItemType (unknown names kept)."""
    for category, names in _POKER_TYPE_NAMES.items():
        if type_name in names:
            return category in include_types
    return True


def _poker_work_item_row(item) -> dict:
    """Normalize one AzDO work item into the poker ticket-row shape.

    {source, key, summary, description, story_points, state, assignee, url}.
    System.Description is HTML — carried raw here; poker/tickets.py adds a
    stripped plain-text variant for display.
    """
    f = item.fields
    pts = f.get("Microsoft.VSTS.Scheduling.StoryPoints")
    try:
        pts = float(pts) if pts is not None else None
    except (TypeError, ValueError):
        pts = None
    assigned_raw = f.get("System.AssignedTo")
    if isinstance(assigned_raw, dict):
        assignee = assigned_raw.get("displayName", "") or ""
    elif assigned_raw:
        assignee = str(assigned_raw)
    else:
        assignee = ""
    wi_id = f.get("System.Id") or getattr(item, "id", "")
    org_url = (get_azure_devops_org_url() or "").rstrip("/")
    project = get_azure_devops_project() or ""
    url = f"{org_url}/{quote(project)}/_workitems/edit/{wi_id}" if org_url and project and wi_id else ""
    return {
        "source": "azdevops",
        "key": str(wi_id),
        "summary": f.get("System.Title", "") or "",
        "description": f.get("System.Description", "") or "",
        "story_points": pts,
        "state": f.get("System.State", "") or "",
        "assignee": assignee,
        "url": url,
        "type": f.get("System.WorkItemType", "") or "",
        "acceptance": f.get("Microsoft.VSTS.Common.AcceptanceCriteria", "") or "",
    }


def _fetch_work_item_rows(
    wit_client, ids: list, limit: int, include_types: tuple[str, ...] | None = None
) -> list[dict]:
    """Batch-fetch work items by id (<=200 per call) as normalized rows.

    The type filter runs per batch and fetching continues until `limit`
    SURVIVING rows are collected (or ids run out) — capping ids up front would
    under-fill the page whenever the filter drops items.
    """
    rows: list[dict] = []
    for start in range(0, len(ids), _WI_BATCH):
        batch = ids[start : start + _WI_BATCH]
        items = wit_client.get_work_items(batch, fields=_POKER_WI_FIELDS)
        for item in items or []:
            row = _poker_work_item_row(item)
            if include_types is not None and not _work_item_type_allowed(row["type"], include_types):
                continue
            rows.append(row)
            if len(rows) >= limit:
                return rows
    return rows


def azdevops_sprint_issues(
    iteration_id: str,
    project: str = "",
    limit: int = 100,
    *,
    include_types: tuple[str, ...] | None = None,
) -> list[dict]:
    """Return the work items in one iteration as normalized poker ticket rows.

    iteration_id comes from azdevops_list_sprints. get_iteration_work_items
    returns EVERY item in the iteration (child Tasks included), so the
    category filter here is what keeps task-level items out of a poker
    session; None = no filter. Returns [] when unconfigured, the iteration is
    empty, or the query fails (logged).
    """
    project = project or get_azure_devops_project() or ""
    logger.info(
        "azdevops_sprint_issues: iteration_id=%r project=%r limit=%d types=%s",
        iteration_id,
        project,
        limit,
        ",".join(include_types) if include_types else "all",
    )
    if not project or not iteration_id:
        return []
    try:
        from azure.devops.v7_1.work.models import TeamContext

        wit_client, work_client = _make_azdo_clients()
        team = get_azure_devops_team() or f"{project} Team"
        team_context = TeamContext(project=project, team=team)
        work_items = work_client.get_iteration_work_items(team_context, iteration_id)
        wi_ids = [
            rel.target.id
            for rel in getattr(work_items, "work_item_relations", []) or []
            if getattr(rel, "target", None)
        ]
        rows = _fetch_work_item_rows(wit_client, wi_ids, limit, include_types)
        logger.info(
            "azdevops_sprint_issues: %d work item(s) after type filter (%d in iteration)", len(rows), len(wi_ids)
        )
        return rows
    except ValueError as e:
        logger.warning("azdevops_sprint_issues skipped: %s", e)
        return []
    except AzureDevOpsServiceError as e:
        _raise_if_azdo_auth(e)
        logger.warning("azdevops_sprint_issues failed: %s", _azdo_error_msg(e))
        return []
    except Exception as e:
        logger.warning("azdevops_sprint_issues unexpected error: %s", e)
        return []


def azdevops_backlog_issues(
    project: str = "",
    limit: int = 100,
    *,
    include_types: tuple[str, ...] | None = None,
) -> list[dict]:
    """Return the project's backlog (open work items in the root iteration).

    "Backlog" = work items whose IterationPath is the project root, i.e. not
    scheduled into any sprint — the AzDO convention. Ordered by last change
    (WIQL backlog-rank fields differ per process template, so ChangedDate is
    the portable ordering). include_types narrows the WIQL type list; None
    keeps the historical stories/PBIs/bugs set. Returns [] when unconfigured
    or on failure (logged).
    """
    project = project or get_azure_devops_project() or ""
    logger.info(
        "azdevops_backlog_issues: project=%r limit=%d types=%s",
        project,
        limit,
        ",".join(include_types) if include_types else "all",
    )
    if not project:
        return []
    try:
        from azure.devops.v7_1.work_item_tracking.models import Wiql

        wit_client, _work_client = _make_azdo_clients()
        # SECURITY: project comes from config (not the LLM) but is still escaped
        # per WIQL rules (double the single quotes) — WIQL has no bind parameters.
        safe_project = project.replace("'", "''")
        # The WIQL type list IS the filter here (server-side, unlike the sprint
        # fetch): selected categories map to their type names; None keeps the
        # historical default set.
        if include_types is None:
            type_names: tuple[str, ...] = _POKER_BACKLOG_TYPES
        else:
            type_names = tuple(name for cat in include_types for name in _POKER_TYPE_NAMES.get(cat, ()))
        if not type_names:
            type_names = _POKER_BACKLOG_TYPES
        types = ", ".join(f"'{t}'" for t in type_names)
        states = ", ".join(f"'{s}'" for s in _POKER_CLOSED_STATES)
        wiql = Wiql(
            query=(
                f"SELECT [System.Id] FROM WorkItems"  # noqa: S608
                f" WHERE [System.TeamProject] = '{safe_project}'"
                f" AND [System.IterationPath] = '{safe_project}'"
                f" AND [System.WorkItemType] IN ({types})"
                f" AND [System.State] NOT IN ({states})"
                f" ORDER BY [System.ChangedDate] DESC"
            )
        )
        result = wit_client.query_by_wiql(wiql, top=limit)
        wi_ids = [wi.id for wi in result.work_items or []]
        rows = _fetch_work_item_rows(wit_client, wi_ids, limit)
        logger.info("azdevops_backlog_issues: %d work item(s)", len(rows))
        return rows
    except ValueError as e:
        logger.warning("azdevops_backlog_issues skipped: %s", e)
        return []
    except AzureDevOpsServiceError as e:
        _raise_if_azdo_auth(e)
        logger.warning("azdevops_backlog_issues failed: %s", _azdo_error_msg(e))
        return []
    except Exception as e:
        logger.warning("azdevops_backlog_issues unexpected error: %s", e)
        return []


def azdevops_update_work_item_fields(
    work_item_id: int,
    *,
    summary: str | None = None,
    description: str | None = None,
    story_points: float | None = None,
    project: str = "",
) -> tuple[bool, str]:
    """Update fields on an existing work item. Returns (ok, human_error).

    Auth errors are folded into the tuple (never raised) — this runs inside a
    live poker session that must not die on a tracker failure. Uses op="add",
    which AzDO treats as add-or-replace: op="replace" fails on a field with no
    current value (an unestimated ticket's StoryPoints, exactly the poker case).
    """
    project = project or get_azure_devops_project() or ""
    logger.info(
        "azdevops_update_work_item_fields: id=%r summary=%s description=%s points=%r",
        work_item_id,
        summary is not None,
        description is not None,
        story_points,
    )
    try:
        from azure.devops.v7_1.work_item_tracking.models import JsonPatchOperation

        wit_client, _work_client = _make_azdo_clients()
        document = []
        if summary is not None:
            document.append(JsonPatchOperation(op="add", path="/fields/System.Title", value=summary))
        if description is not None:
            document.append(JsonPatchOperation(op="add", path="/fields/System.Description", value=description))
        if story_points is not None:
            document.append(
                JsonPatchOperation(
                    op="add",
                    path="/fields/Microsoft.VSTS.Scheduling.StoryPoints",
                    value=float(story_points),
                )
            )
        if not document:
            return True, ""
        wit_client.update_work_item(document=document, id=int(work_item_id), project=project)
        logger.info("azdevops_update_work_item_fields: #%s updated", work_item_id)
        return True, ""
    except ValueError as e:
        logger.warning("azdevops_update_work_item_fields skipped: %s", e)
        return False, f"Error: {e}"
    except AzureDevOpsServiceError as e:
        logger.warning("azdevops_update_work_item_fields failed: %s", _azdo_error_msg(e))
        return False, _azdo_error_msg(e)
    except Exception as e:
        logger.warning("azdevops_update_work_item_fields unexpected error: %s", e)
        return False, f"Error: {e}"
