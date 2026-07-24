"""Batch Azure DevOps creation with idempotency, progress callbacks, and error accumulation.

# See docs: "Tools" — tool types, write tools, human-in-the-loop pattern
#
# This module orchestrates creating Azure DevOps resources (Epic, User Stories,
# Tasks, Iterations) from the scrum agent's generated artifacts. It is called by
# the TUI pipeline review screens — NOT by the ReAct agent.
#
# Idempotency: each sync function checks the azdevops_*_keys dicts in graph_state
# before creating anything. Already-created items are skipped. This makes it
# safe to re-run after partial failures.
#
# Semantic mapping:
#   Features → Tags (System.Tags, semicolon-separated)
#   1 project-level Epic work item (project name as title)
#   UserStories → User Story work items linked to the Epic
#   Tasks → Task work items linked to their parent Story
#   Sprints → Iterations (classification nodes) with stories assigned via IterationPath
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from yeaboi.config import (
    get_azure_devops_org_url,
    get_azure_devops_project,
    get_azure_devops_token,
)

logger = logging.getLogger(__name__)

# Type alias for progress callbacks: (current, total, description)
ProgressCallback = Callable[[int, int, str], None]


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class AzDevOpsSyncResult:
    """Accumulates results from a batch Azure DevOps sync operation."""

    epic_id: str | None = None
    stories_created: dict[str, str] = field(default_factory=dict)  # internal_id → work_item_id
    tasks_created: dict[str, str] = field(default_factory=dict)
    iterations_created: dict[str, str] = field(default_factory=dict)  # internal_id → iteration_path
    errors: list[str] = field(default_factory=list)
    skipped: int = 0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def is_azdevops_board_configured() -> bool:
    """Return True if Azure DevOps board credentials are present in the environment."""
    return bool(get_azure_devops_token() and get_azure_devops_org_url() and get_azure_devops_project())


def sync_stories_to_azdevops(
    graph_state: dict[str, Any],
    on_progress: ProgressCallback | None = None,
) -> tuple[AzDevOpsSyncResult, dict[str, Any]]:
    """Create a project Epic and User Stories in Azure DevOps, skipping already-created items.

    Returns (result, updated_graph_state).
    """
    from yeaboi.tools.azure_devops import _make_azdo_clients

    result = AzDevOpsSyncResult()
    state = dict(graph_state)  # shallow copy to avoid mutating caller's dict

    project = get_azure_devops_project() or ""
    if not project:
        result.errors.append("AZURE_DEVOPS_PROJECT not set.")
        return result, state

    org_url = get_azure_devops_org_url() or ""
    if not org_url:
        result.errors.append("AZURE_DEVOPS_ORG_URL not set.")
        return result, state

    try:
        from azure.devops.v7_1.work_item_tracking.models import JsonPatchOperation

        wit_client = _make_azdo_clients(org_url, get_azure_devops_token())[0]
    except Exception as e:
        result.errors.append(f"Azure DevOps connection failed: {e}")
        return result, state

    # Area path = "{project}\{team}" — assigns work items to the team's board area.
    from yeaboi.config import get_azure_devops_team as _get_team

    team = _get_team() or ""
    area_path = f"{project}\\{team}" if team else project

    stories = state.get("stories", [])
    features = state.get("features", [])
    feature_map = {f.id: f for f in features}
    existing_story_keys: dict[str, str] = dict(state.get("azdevops_story_keys", {}))

    # Total items = 1 (epic) + ALL stories
    total = 1 + len(stories)
    current = 0

    # --- Epic ---
    epic_id = state.get("azdevops_epic_id", "")
    if not epic_id:
        try:
            analysis = state.get("project_analysis")
            epic_title = getattr(analysis, "project_name", None) or state.get("project_name", "Project")
            epic_desc = getattr(analysis, "project_description", None) or ""

            document = [
                JsonPatchOperation(op="add", path="/fields/System.Title", value=epic_title),
                JsonPatchOperation(op="add", path="/fields/System.Description", value=epic_desc),
                JsonPatchOperation(op="add", path="/fields/System.AreaPath", value=area_path),
            ]
            work_item = wit_client.create_work_item(document=document, project=project, type="Epic")
            epic_id = str(work_item.id)
            state["azdevops_epic_id"] = epic_id
            result.epic_id = epic_id
            logger.info("Created AzDO Epic: %s (ID: %s)", epic_title, epic_id)
        except Exception as e:
            result.errors.append(f"Epic creation failed: {e}")
            return result, state
    else:
        result.epic_id = epic_id
        result.skipped += 1

    current += 1
    if on_progress:
        on_progress(current, total, f"Epic: {epic_id}")

    # --- Stories ---
    new_story_keys: dict[str, str] = {}

    for story in stories:
        feature = feature_map.get(story.feature_id)

        if story.id in existing_story_keys:
            # Story already exists — update its description (DoD, rationale may have been added)
            wi_id = existing_story_keys[story.id]
            try:
                from azure.devops.v7_1.work_item_tracking.models import JsonPatchOperation as _Jpo

                description = _format_story_description_html(story, feature)
                doc = [_Jpo(op="replace", path="/fields/System.Description", value=description)]
                wit_client.update_work_item(document=doc, id=int(wi_id), project=project)
                logger.info("Updated AzDO Story description: %s", wi_id)
                time.sleep(0.1)
            except Exception as e:
                logger.warning("Could not update work item %s: %s", wi_id, e)
            result.skipped += 1
            current += 1
            if on_progress:
                on_progress(current, total, f"Story updated: {story.title or story.goal[:40]}")
            continue

        try:
            # Build tags from feature title + discipline
            tags: list[str] = []
            if feature:
                tags.append(_feature_title_to_tag(feature.title))
            disc = story.discipline
            tags.append(disc.value if hasattr(disc, "value") else str(disc))
            tags_str = "; ".join(tags)

            summary = story.title or story.goal
            description = _format_story_description_html(story, feature)
            raw_pri = story.priority
            priority_val = _map_priority_to_azdo(raw_pri.value if hasattr(raw_pri, "value") else str(raw_pri))

            document = [
                JsonPatchOperation(op="add", path="/fields/System.Title", value=summary),
                JsonPatchOperation(op="add", path="/fields/System.Description", value=description),
                JsonPatchOperation(op="add", path="/fields/Microsoft.VSTS.Common.Priority", value=priority_val),
                JsonPatchOperation(op="add", path="/fields/System.Tags", value=tags_str),
                JsonPatchOperation(op="add", path="/fields/System.AreaPath", value=area_path),
            ]

            if story.story_points:
                document.append(
                    JsonPatchOperation(
                        op="add",
                        path="/fields/Microsoft.VSTS.Scheduling.StoryPoints",
                        value=float(int(story.story_points)),
                    )
                )

            # Link to parent Epic
            if epic_id:
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
            new_story_keys[story.id] = wi_id
            result.stories_created[story.id] = wi_id
            logger.info("Created AzDO User Story: %s → %s", story.id, wi_id)

            time.sleep(0.1)  # Brief delay to avoid rate limiting
        except Exception as e:
            err = f"Story '{story.title or story.id}': {e}"
            logger.error("AzDO sync failed — %s", err)
            result.errors.append(err)

        current += 1
        if on_progress:
            on_progress(current, total, f"Story created: {story.title or story.goal[:40]}")

    # Merge new keys into state
    merged_story_keys = {**existing_story_keys, **new_story_keys}
    state["azdevops_story_keys"] = merged_story_keys

    return result, state


def sync_tasks_to_azdevops(
    graph_state: dict[str, Any],
    on_progress: ProgressCallback | None = None,
) -> tuple[AzDevOpsSyncResult, dict[str, Any]]:
    """Create Azure DevOps Tasks for each task, cascading to create stories first if needed.

    Returns (result, updated_graph_state).
    """
    from yeaboi.tools.azure_devops import create_task

    state = dict(graph_state)

    # Cascade: create stories first if not done
    story_keys = state.get("azdevops_story_keys", {})
    stories = state.get("stories", [])
    if stories and not story_keys:
        story_result, state = sync_stories_to_azdevops(state, on_progress)
        story_keys = state.get("azdevops_story_keys", {})
        if story_result.errors and not story_keys:
            return story_result, state

    result = AzDevOpsSyncResult(epic_id=state.get("azdevops_epic_id"))
    result.stories_created = {
        k: v for k, v in story_keys.items() if k not in graph_state.get("azdevops_story_keys", {})
    }

    project = get_azure_devops_project() or ""
    tasks = state.get("tasks", [])
    existing_task_keys: dict[str, str] = dict(state.get("azdevops_task_keys", {}))

    total = len(tasks)
    current = 0
    new_task_keys: dict[str, str] = {}

    for task in tasks:
        if task.id in existing_task_keys:
            # Task already exists — update its description (ai_prompt may have been added)
            wi_id = existing_task_keys[task.id]
            try:
                from azure.devops.v7_1.work_item_tracking.models import JsonPatchOperation as _Jpo

                from yeaboi.tools.azure_devops import _make_azdo_clients as _mc

                _wit = _mc()[0]
                description = _format_task_description_html(task)
                doc = [_Jpo(op="replace", path="/fields/System.Description", value=description)]
                _wit.update_work_item(document=doc, id=int(wi_id), project=project)
                logger.info("Updated AzDO Task description: %s", wi_id)
                time.sleep(0.1)
            except Exception as e:
                logger.warning("Could not update work item %s: %s", wi_id, e)
            result.skipped += 1
            current += 1
            if on_progress:
                on_progress(current, total, f"Task updated: {task.title[:40]}")
            continue

        parent_id = story_keys.get(task.story_id)
        if not parent_id:
            result.errors.append(f"Task '{task.title}': parent story '{task.story_id}' not in Azure DevOps.")
            current += 1
            if on_progress:
                on_progress(current, total, f"Task skipped (no parent): {task.title[:40]}")
            continue

        try:
            description = _format_task_description_html(task)
            task_id = create_task(
                title=task.title,
                description=description,
                story_id=parent_id,
                project=project,
            )
            new_task_keys[task.id] = task_id
            result.tasks_created[task.id] = task_id
            logger.info("Created AzDO Task: %s → %s", task.id, task_id)
            time.sleep(0.1)
        except Exception as e:
            err = f"Task '{task.title}': {e}"
            logger.error("AzDO sync failed — %s", err)
            result.errors.append(err)

        current += 1
        if on_progress:
            on_progress(current, total, f"Task created: {task.title[:40]}")

    merged_task_keys = {**existing_task_keys, **new_task_keys}
    state["azdevops_task_keys"] = merged_task_keys

    return result, state


def sync_iterations_to_azdevops(
    graph_state: dict[str, Any],
    on_progress: ProgressCallback | None = None,
) -> tuple[AzDevOpsSyncResult, dict[str, Any]]:
    """Create Azure DevOps Iterations and assign stories, cascading to create stories first if needed.

    Returns (result, updated_graph_state).
    """
    from yeaboi.tools.azure_devops import add_work_items_to_iteration

    state = dict(graph_state)

    # Cascade: create stories first if not done
    story_keys = state.get("azdevops_story_keys", {})
    stories = state.get("stories", [])
    if stories and not story_keys:
        story_result, state = sync_stories_to_azdevops(state, on_progress)
        story_keys = state.get("azdevops_story_keys", {})
        if story_result.errors and not story_keys:
            return story_result, state

    result = AzDevOpsSyncResult(epic_id=state.get("azdevops_epic_id"))

    project = get_azure_devops_project() or ""
    org_url = get_azure_devops_org_url() or ""
    token = get_azure_devops_token() or ""

    sprints = state.get("sprints", [])
    existing_iteration_keys: dict[str, str] = dict(state.get("azdevops_iteration_keys", {}))

    # Detect existing iteration naming convention (same pattern as jira_sync.py)
    iteration_name_prefix = ""
    max_existing_number = 0
    try:
        from yeaboi.tools.azure_devops import _make_azdo_clients

        _, work_client = _make_azdo_clients(org_url, token)
        from azure.devops.v7_1.work.models import TeamContext

        from yeaboi.config import get_azure_devops_team

        team = get_azure_devops_team() or f"{project} Team"
        team_context = TeamContext(project=project, team=team)
        existing_iters = work_client.get_team_iterations(team_context) or []
        for it in existing_iters:
            match = re.match(r"^(.+?)(\d+)\s*$", it.name or "")
            if match:
                num = int(match.group(2))
                if num > max_existing_number:
                    max_existing_number = num
                    iteration_name_prefix = match.group(1)
        if iteration_name_prefix:
            logger.debug(
                "Detected iteration naming pattern: '%sN' (max: %d)", iteration_name_prefix, max_existing_number
            )
    except Exception as e:
        logger.debug("Could not detect iteration naming pattern: %s", e)

    # Determine starting number for new iterations
    starting_number = state.get("starting_sprint_number", 0)
    if not starting_number and max_existing_number > 0:
        starting_number = max_existing_number + 1

    sprint_length_weeks = state.get("sprint_length_weeks", 2)
    sprint_start_date_str = state.get("sprint_start_date", "")

    total = len(sprints)
    current = 0
    new_iteration_keys: dict[str, str] = {}

    for idx, sprint in enumerate(sprints):
        # Normalize sprint name to match the board's naming convention
        sprint_name = sprint.name
        if iteration_name_prefix and starting_number:
            sprint_number = starting_number + idx
            sprint_name = f"{iteration_name_prefix}{sprint_number}"
            if sprint_name != sprint.name:
                logger.info("Renamed iteration '%s' → '%s' (board convention)", sprint.name, sprint_name)

        if sprint.id in existing_iteration_keys:
            # Already tracked — just assign stories (in case new ones were added)
            iteration_path = existing_iteration_keys[sprint.id]
            issue_ids = [story_keys[sid] for sid in sprint.story_ids if sid in story_keys]
            if issue_ids:
                try:
                    add_work_items_to_iteration(issue_ids, iteration_path, project)
                except Exception as e:
                    logger.warning("Could not update iteration %s items: %s", sprint_name, e)
            result.skipped += 1
            current += 1
            if on_progress:
                on_progress(current, total, f"Iteration updated: {sprint_name}")
            continue

        try:
            # Compute iteration dates
            start_date = ""
            finish_date = ""
            if sprint_start_date_str:
                from datetime import datetime, timedelta

                start = datetime.fromisoformat(sprint_start_date_str) + timedelta(weeks=sprint_length_weeks * idx)
                end = start + timedelta(weeks=sprint_length_weeks)
                start_date = start.strftime("%Y-%m-%d")
                finish_date = end.strftime("%Y-%m-%d")

            # Create iteration as a classification node via REST API
            iteration_path = _create_iteration_node(
                org_url,
                token,
                project,
                sprint_name,
                start_date=start_date,
                finish_date=finish_date,
            )

            new_iteration_keys[sprint.id] = iteration_path
            result.iterations_created[sprint.id] = iteration_path

            # Assign stories to iteration
            issue_ids = [story_keys[sid] for sid in sprint.story_ids if sid in story_keys]
            if issue_ids:
                add_work_items_to_iteration(issue_ids, iteration_path, project)

            logger.info("Created AzDO Iteration: %s → %s", sprint_name, iteration_path)
            time.sleep(0.1)
        except Exception as e:
            err = f"Iteration '{sprint_name}': {e}"
            logger.error("AzDO sync failed — %s", err)
            result.errors.append(err)

        current += 1
        if on_progress:
            on_progress(current, total, f"Iteration created: {sprint_name}")

    merged_iteration_keys = {**existing_iteration_keys, **new_iteration_keys}
    state["azdevops_iteration_keys"] = merged_iteration_keys

    return result, state


def sync_all_to_azdevops(
    graph_state: dict[str, Any],
    on_progress: ProgressCallback | None = None,
) -> tuple[AzDevOpsSyncResult, dict[str, Any]]:
    """Full sync: Epic + Stories + Tasks + Iterations, aggregating results.

    Returns (aggregated_result, updated_graph_state).
    """
    state = dict(graph_state)
    aggregated = AzDevOpsSyncResult()

    # Stories (includes Epic creation)
    story_result, state = sync_stories_to_azdevops(state, on_progress)
    aggregated.epic_id = story_result.epic_id
    aggregated.stories_created.update(story_result.stories_created)
    aggregated.errors.extend(story_result.errors)
    aggregated.skipped += story_result.skipped

    # Tasks
    if state.get("tasks"):
        task_result, state = sync_tasks_to_azdevops(state, on_progress)
        aggregated.tasks_created.update(task_result.tasks_created)
        aggregated.errors.extend(task_result.errors)
        aggregated.skipped += task_result.skipped

    # Iterations
    if state.get("sprints"):
        iter_result, state = sync_iterations_to_azdevops(state, on_progress)
        aggregated.iterations_created.update(iter_result.iterations_created)
        aggregated.errors.extend(iter_result.errors)
        aggregated.skipped += iter_result.skipped

    return aggregated, state


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


# Map internal Priority enum values to Azure DevOps priority integers.
# AzDO Priority: 1=Critical, 2=High, 3=Medium, 4=Low.
_PRIORITY_TO_AZDO: dict[str, int] = {
    "critical": 1,
    "high": 2,
    "medium": 3,
    "low": 4,
}


def _map_priority_to_azdo(priority_value: str) -> int:
    """Map an internal priority string to an Azure DevOps priority integer."""
    return _PRIORITY_TO_AZDO.get(priority_value, 3)


def _feature_title_to_tag(title: str) -> str:
    """Sanitize a feature title for use as an Azure DevOps tag.

    AzDO tags are semicolon-separated and allow spaces (unlike Jira labels).
    Strip special characters but keep spaces; limit length.
    """
    if not title:
        return "Feature"
    # Strip characters that could interfere with semicolon-separated tag format
    tag = re.sub(r"[;,\n\r]", "", title.strip())
    return tag[:80] or "Feature"


def _format_story_description_html(story, feature=None) -> str:
    """Format a UserStory as an HTML description for Azure DevOps."""
    from yeaboi.agent.state import DOD_ITEMS

    parts: list[str] = []

    # User story sentence
    parts.append(
        f"<p><strong>As a</strong> {story.persona}, <strong>I want to</strong> {story.goal}, "
        f"<strong>so that</strong> {story.benefit}.</p>"
    )

    # Acceptance criteria
    if story.acceptance_criteria:
        parts.append("<h3>Acceptance Criteria</h3>")
        for i, ac in enumerate(story.acceptance_criteria, 1):
            parts.append(f"<p><strong>AC{i}</strong></p>")
            parts.append("<ul>")
            parts.append(f"<li><strong>Given</strong> {ac.given}</li>")
            parts.append(f"<li><strong>When</strong> {ac.when}</li>")
            parts.append(f"<li><strong>Then</strong> {ac.then}</li>")
            parts.append("</ul>")

    # Definition of Done
    dod = getattr(story, "dod_applicable", None)
    if dod and len(dod) == len(DOD_ITEMS):
        parts.append("<h3>Definition of Done</h3>")
        parts.append("<ul>")
        for item, applicable in zip(DOD_ITEMS, dod):
            if applicable:
                parts.append(f"<li>&#9745; {item}</li>")
            else:
                parts.append(f"<li>&#9744; <s>{item}</s></li>")
        parts.append("</ul>")

    # Points rationale
    rationale = getattr(story, "points_rationale", "")
    if rationale:
        parts.append("<h3>Points Rationale</h3>")
        parts.append(f"<p>{rationale}</p>")

    # Feature context
    if feature:
        parts.append(f"<p><em>Feature: {feature.title}</em></p>")

    return "\n".join(parts)


def _format_task_description_html(task) -> str:
    """Format a Task as an HTML description for Azure DevOps."""
    parts: list[str] = []
    if task.description:
        parts.append(f"<p>{task.description}</p>")

    if hasattr(task, "test_plan") and task.test_plan:
        parts.append("<h3>Test Plan</h3>")
        parts.append(f"<p>{task.test_plan}</p>")

    if hasattr(task, "ai_prompt") and task.ai_prompt:
        parts.append("<h3>AI Prompt</h3>")
        parts.append(f"<p>{task.ai_prompt}</p>")

    return "\n".join(parts)


def _create_iteration_node(
    org_url: str,
    token: str,
    project: str,
    name: str,
    start_date: str = "",
    finish_date: str = "",
) -> str:
    """Create an iteration classification node and assign it to the team.

    Two-step process:
    1. Create the iteration at the project level (Classification Nodes API)
    2. Assign it to the team (Team Settings Iterations API)

    Without step 2, work items can't use the iteration path because it's
    not valid for the team's board.

    Returns the full iteration path (e.g. "MyProject\\Sprint 1").
    start_date / finish_date are ISO date strings (e.g. "2026-03-16").
    """
    import base64

    import httpx

    b64 = base64.b64encode(f":{token}".encode()).decode()
    auth_headers = {
        "Authorization": f"Basic {b64}",
        "Content-Type": "application/json",
    }

    # Step 1: Create iteration as a classification node
    create_url = f"{org_url}/{project}/_apis/wit/classificationnodes/Iterations?api-version=7.1"

    # AzDO requires full ISO 8601 with time component for iteration dates.
    # Convert "2026-03-16" → "2026-03-16T00:00:00Z" if needed.
    def _to_iso(d: str) -> str:
        return f"{d}T00:00:00Z" if d and "T" not in d else d

    body: dict = {"name": name}
    if start_date or finish_date:
        body["attributes"] = {}
        if start_date:
            body["attributes"]["startDate"] = _to_iso(start_date)
        if finish_date:
            body["attributes"]["finishDate"] = _to_iso(finish_date)

    resp = httpx.post(create_url, headers=auth_headers, json=body, timeout=15)

    if resp.status_code in (200, 201):
        data = resp.json()
        iteration_id = str(data.get("identifier", data.get("id", "")))
        iteration_path = data.get("path", f"\\{project}\\{name}").lstrip("\\")
    elif resp.status_code == 409:
        # Iteration already exists — fetch its ID so we can assign it to the team
        logger.info("Iteration '%s' already exists in %s — fetching ID", name, project)
        iteration_path = f"{project}\\{name}"
        # GET the existing node to find its identifier
        get_url = f"{org_url}/{project}/_apis/wit/classificationnodes/Iterations/{name}?api-version=7.1"
        get_resp = httpx.get(get_url, headers=auth_headers, timeout=15)
        if get_resp.status_code == 200:
            iteration_id = str(get_resp.json().get("identifier", ""))
        else:
            iteration_id = ""
    else:
        raise RuntimeError(f"Failed to create iteration '{name}': HTTP {resp.status_code} — {resp.text}")

    # Step 2: Assign iteration to the team so work items can use this IterationPath
    if iteration_id:
        from yeaboi.config import get_azure_devops_team as _get_team

        team = _get_team() or f"{project} Team"
        assign_url = f"{org_url}/{project}/{team}/_apis/work/teamsettings/iterations?api-version=7.1"
        assign_body = {"id": iteration_id}
        try:
            assign_resp = httpx.post(assign_url, headers=auth_headers, json=assign_body, timeout=15)
            if assign_resp.status_code in (200, 201):
                logger.info("Assigned iteration '%s' to team '%s'", name, team)
            elif assign_resp.status_code == 409:
                logger.debug("Iteration '%s' already assigned to team '%s'", name, team)
            else:
                logger.warning(
                    "Could not assign iteration '%s' to team '%s': HTTP %d — %s",
                    name,
                    team,
                    assign_resp.status_code,
                    assign_resp.text,
                )
        except Exception as e:
            logger.warning("Could not assign iteration '%s' to team: %s", name, e)

    return iteration_path
