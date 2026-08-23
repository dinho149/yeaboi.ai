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
from yeaboi.timeparse import parse_datetime

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
    iterations_updated: dict[str, str] = field(default_factory=dict)  # existing iterations that gained items
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

    # Descriptions render against the plan's OWN DoD list and the team's own
    # section headings — never the hardcoded defaults.
    from yeaboi.agent.state import map_template_headings, resolve_dod_items

    dod_items = resolve_dod_items(state)
    headings = map_template_headings(tuple(state.get("ticket_template_sections") or ()))

    for story in stories:
        feature = feature_map.get(story.feature_id)

        if story.id in existing_story_keys:
            # Story already exists — update its description (DoD, rationale may have been added)
            wi_id = existing_story_keys[story.id]
            try:
                from azure.devops.v7_1.work_item_tracking.models import JsonPatchOperation as _Jpo

                description = _format_story_description_html(story, feature, dod_items=dod_items, headings=headings)
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
            description = _format_story_description_html(story, feature, dod_items=dod_items, headings=headings)
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
    cascade_stories: dict[str, str] = {}
    cascade_errors: list[str] = []
    if stories and not story_keys:
        story_result, state = sync_stories_to_azdevops(state, on_progress)
        story_keys = state.get("azdevops_story_keys", {})
        if story_result.errors and not story_keys:
            return story_result, state
        cascade_stories = dict(story_result.stories_created)
        cascade_errors = list(story_result.errors)

    result = AzDevOpsSyncResult(epic_id=state.get("azdevops_epic_id"))
    # Cascade-created stories are reported in the stories bucket, not silently dropped.
    result.stories_created.update(cascade_stories)
    result.errors.extend(cascade_errors)

    # Backlog target (small-project intake): the stories above are the whole
    # sync — nothing is created or assigned; unassigned items sit in the backlog.
    if state.get("sprint_target_mode") == "backlog":
        logger.info("Iteration sync: backlog mode — stories stay in the backlog, no iteration created")
        if on_progress:
            on_progress(1, 1, "Stories left in the backlog — no iteration created")
        return result, state

    project = get_azure_devops_project() or ""
    org_url = get_azure_devops_org_url() or ""
    token = get_azure_devops_token() or ""

    sprints = state.get("sprints", [])
    existing_iteration_keys: dict[str, str] = dict(state.get("azdevops_iteration_keys", {}))

    # Detect existing iteration naming convention (same consensus derivation as
    # jira_sync.py); "past" iterations map onto the closed-sprint guard.
    from yeaboi.sync_naming import advance_past_closed, derive_board_numbering, resolve_starting_number

    time_frame_to_state = {"past": "closed", "current": "active", "future": "future"}
    iteration_meta: list[dict] = []
    try:
        from yeaboi.tools.azure_devops import fetch_team_iterations_meta

        iteration_meta = fetch_team_iterations_meta(org_url, token, project)
    except Exception as e:
        # Without the metadata the sync silently reverts to generic numbering
        # (and an existing-target sync cannot resolve by name) — say so.
        logger.warning("Could not fetch iteration metadata: %s — falling back to generic numbering", e)

    # "Add to an existing iteration" (small-project intake): assign the plan's
    # stories to the chosen iteration and never create one.
    if state.get("sprint_target_mode") == "existing":
        return _sync_to_existing_azdo_iteration(state, result, story_keys, iteration_meta, project, on_progress)

    numbering = derive_board_numbering(
        (it["name"], time_frame_to_state.get(it["time_frame"], "future")) for it in iteration_meta
    )
    iteration_name_prefix = numbering.prefix

    # Determine starting number for new iterations. The intake's -1 "no tracker
    # sprint picked" sentinel falls through to max+1, and a batch that would
    # land on a past iteration's name is shifted forward as one block.
    starting_number = resolve_starting_number(state.get("starting_sprint_number", 0), numbering)
    starting_number, closed_warn = advance_past_closed(starting_number, len(sprints), numbering)
    if closed_warn:
        logger.warning("%s", closed_warn)
        if on_progress:
            on_progress(0, len(sprints), closed_warn)
    if iteration_name_prefix:
        logger.info(
            "Iteration naming: prefix=%r max_existing=%d starting_number=%d",
            iteration_name_prefix,
            numbering.max_number,
            starting_number,
        )

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
            # Reuse a same-named iteration only while it can still take items —
            # past iterations are never a target (mirrors the Jira closed-sprint
            # guard; numbered collisions were already renumbered above).
            matched = next(
                (it for it in iteration_meta if it["name"] == sprint_name and it["time_frame"] != "past"),
                None,
            )
            if matched:
                iteration_path = matched["path"].lstrip("\\") or f"{project}\\{sprint_name}"
                result.iterations_updated[sprint.id] = iteration_path
                progress_label = f"Iteration reused: {sprint_name}"
                logger.info("Reusing existing AzDO Iteration: %s → %s", sprint_name, iteration_path)
            else:
                # Compute iteration dates. End is inclusive (start + length − 1
                # day) so consecutive iterations don't overlap — same convention
                # as reporting/sprints.py and the Jira sync.
                start_date = ""
                finish_date = ""
                if sprint_start_date_str:
                    from datetime import timedelta

                    start = parse_datetime(sprint_start_date_str) + timedelta(weeks=sprint_length_weeks * idx)
                    end = start + timedelta(weeks=sprint_length_weeks) - timedelta(days=1)
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
                result.iterations_created[sprint.id] = iteration_path
                progress_label = f"Iteration created: {sprint_name}"
                logger.info("Created AzDO Iteration: %s → %s", sprint_name, iteration_path)

            new_iteration_keys[sprint.id] = iteration_path

            # Assign stories to iteration
            issue_ids = [story_keys[sid] for sid in sprint.story_ids if sid in story_keys]
            if issue_ids:
                add_work_items_to_iteration(issue_ids, iteration_path, project)

            time.sleep(0.1)
        except Exception as e:
            err = f"Iteration '{sprint_name}': {e}"
            logger.error("AzDO sync failed — %s", err)
            result.errors.append(err)
            progress_label = f"Iteration failed: {sprint_name}"

        current += 1
        if on_progress:
            on_progress(current, total, progress_label)

    merged_iteration_keys = {**existing_iteration_keys, **new_iteration_keys}
    state["azdevops_iteration_keys"] = merged_iteration_keys

    return result, state


def _sync_to_existing_azdo_iteration(
    state: dict[str, Any],
    result: AzDevOpsSyncResult,
    story_keys: dict[str, str],
    iteration_meta: list[dict],
    project: str,
    on_progress: ProgressCallback | None,
) -> tuple[AzDevOpsSyncResult, dict[str, Any]]:
    """Assign the plan's stories to an existing iteration — never create one.

    Mirror of _sync_to_existing_jira_sprint: resolve by the intake's captured
    iteration path first, else by name among current/future iterations; a past
    (or missing) target errors loudly instead of falling back to creation.
    """
    from yeaboi.tools.azure_devops import add_work_items_to_iteration

    target_name = str(state.get("target_sprint_name") or "")
    target_ext = str(state.get("target_sprint_external_id") or "")

    open_iters = {it["name"]: it["path"] for it in iteration_meta if it["time_frame"] != "past"}
    target_path = ""
    if target_ext:
        # An external path is validated against the team's iterations like a
        # name is — a past (or unknown) path must not slip past the filter.
        # Empty metadata means the fetch failed (already logged); then the
        # path goes through best-effort and the API errors loudly.
        open_paths = {p.lstrip("\\") for p in open_iters.values()}
        if not iteration_meta or target_ext.lstrip("\\") in open_paths:
            target_path = target_ext
    elif target_name:
        target_path = open_iters.get(target_name, "")
        if not target_path:
            folded = {name.casefold(): path for name, path in open_iters.items()}
            target_path = folded.get(target_name.casefold(), "")

    if not target_path:
        result.errors.append(
            f"Iteration '{target_name or target_ext}' not found among current/future iterations — "
            "nothing was created. Pick a different iteration or re-run without an existing-sprint target."
        )
        logger.error("Existing-iteration sync: target %r not resolvable", target_name or target_ext)
        return result, state

    target_path = target_path.lstrip("\\")
    sprints = state.get("sprints", [])
    issue_ids = sorted({story_keys[sid] for sprint in sprints for sid in sprint.story_ids if sid in story_keys})
    label = target_name or target_path
    try:
        add_work_items_to_iteration(issue_ids, target_path, project)
    except Exception as e:
        result.errors.append(f"Could not add stories to iteration '{label}': {e}")
        logger.error("Existing-iteration sync failed — %s", e)
        return result, state

    merged_keys = dict(state.get("azdevops_iteration_keys", {}))
    for sprint in sprints:
        merged_keys[sprint.id] = target_path
        result.iterations_updated[sprint.id] = target_path
    state["azdevops_iteration_keys"] = merged_keys
    logger.info("Added %d story(ies) to existing iteration %s", len(issue_ids), label)
    if on_progress:
        on_progress(len(sprints), len(sprints), f"Stories added to {label}")
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
        aggregated.iterations_updated.update(iter_result.iterations_updated)
        aggregated.stories_created.update(iter_result.stories_created)
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


def _format_story_description_html(story, feature=None, *, dod_items=None, headings=None) -> str:
    """Format a UserStory as an HTML description for Azure DevOps.

    Mirror of jira_sync._format_story_description: renders against the plan's
    resolved DoD list (not the hardcoded default) and adopts the team's own
    section headings where learned.
    """
    from yeaboi.agent.state import DOD_ITEMS

    items = tuple(dod_items) if dod_items else DOD_ITEMS
    headings = headings or {}
    parts: list[str] = []

    # User story sentence — under the team's summary heading when they have one
    if headings.get("summary"):
        parts.append(f"<h3>{headings['summary']}</h3>")
    parts.append(
        f"<p><strong>As a</strong> {story.persona}, <strong>I want to</strong> {story.goal}, "
        f"<strong>so that</strong> {story.benefit}.</p>"
    )

    # Acceptance criteria — GWT triples or the team's free-text criteria
    if story.acceptance_criteria:
        parts.append(f"<h3>{headings.get('acceptance_criteria', 'Acceptance Criteria')}</h3>")
        free_text = [ac for ac in story.acceptance_criteria if ac.text]
        if len(free_text) == len(story.acceptance_criteria):
            parts.append("<ul>")
            for ac in story.acceptance_criteria:
                parts.append(f"<li>{ac.text}</li>")
            parts.append("</ul>")
        else:
            gwt_count = 0  # numbers the GWT triples only, so a mixed list reads AC1, AC2, …
            for ac in story.acceptance_criteria:
                if ac.text:
                    parts.append(f"<p>{ac.text}</p>")
                    continue
                gwt_count += 1
                parts.append(f"<p><strong>AC{gwt_count}</strong></p>")
                parts.append("<ul>")
                parts.append(f"<li><strong>Given</strong> {ac.given}</li>")
                parts.append(f"<li><strong>When</strong> {ac.when}</li>")
                parts.append(f"<li><strong>Then</strong> {ac.then}</li>")
                parts.append("</ul>")

    # Definition of Done. Flags are positional against the resolved list;
    # stories from older sessions carry flags sized to the default 7-item
    # list, so a length mismatch pads with applicable / drops extras rather
    # than losing the whole section.
    dod = getattr(story, "dod_applicable", None)
    if dod:
        parts.append(f"<h3>{headings.get('dod', 'Definition of Done')}</h3>")
        parts.append("<ul>")
        for i, item in enumerate(items):
            applicable = dod[i] if i < len(dod) else True
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
