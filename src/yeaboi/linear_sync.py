"""Batch Linear creation with idempotency, progress callbacks, and error accumulation.

# See docs: "Tools" — tool types, write tools, human-in-the-loop pattern
#
# The Linear counterpart of jira_sync.py, called by the TUI pipeline review
# screens, the project export button and MCP plan_sync — NOT by the ReAct agent.
#
# Idempotency: each sync function checks the linear_*_keys dicts in graph_state
# before creating anything. Already-created items are skipped, so a re-run
# after a partial failure is safe.
#
# Semantic mapping (Linear's own Jira-import mapping):
#   1 project-level Linear Project (project name as title)
#   UserStories → Issues in the Project (`estimate` carries the points)
#   Tasks → sub-issues under their parent Issue
#   Sprints → Cycles with issues assigned (the team must have cycles enabled)
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from yeaboi.config import get_linear_api_key

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int, str], None]


@dataclass
class LinearSyncResult:
    """Accumulates results from a batch Linear sync operation."""

    project_id: str | None = None
    stories_created: dict[str, str] = field(default_factory=dict)  # internal_id → issue identifier
    tasks_created: dict[str, str] = field(default_factory=dict)
    cycles_created: dict[str, str] = field(default_factory=dict)  # internal_id → cycle id
    cycles_updated: dict[str, str] = field(default_factory=dict)  # existing cycles that gained issues
    errors: list[str] = field(default_factory=list)
    skipped: int = 0


def is_linear_configured() -> bool:
    """Return True if a Linear API key is present in the environment."""
    return get_linear_api_key() is not None


_PRIORITY_WORD: dict[str, str] = {"critical": "critical", "high": "high", "medium": "medium", "low": "low"}


def sync_stories_to_linear(
    graph_state: dict[str, Any],
    on_progress: ProgressCallback | None = None,
) -> tuple[LinearSyncResult, dict[str, Any]]:
    """Create the Project and its Issues in Linear, skipping already-created items.

    Returns (result, updated_graph_state).
    """
    from yeaboi.tools.linear import LinearError, _label_ids, _linear_request, _resolve_team

    result = LinearSyncResult()
    state = dict(graph_state)

    if not is_linear_configured():
        result.errors.append("Linear not configured — missing LINEAR_API_KEY.")
        return result, state

    try:
        team = _resolve_team()
    except LinearError as e:
        result.errors.append(str(e).removeprefix("Error: "))
        return result, state

    stories = state.get("stories", [])
    features = state.get("features", [])
    feature_map = {f.id: f for f in features}
    existing_story_keys: dict[str, str] = dict(state.get("linear_story_keys", {}))
    # Identifier → Linear issue uuid, for cycle assignment later.
    issue_ids: dict[str, str] = dict(state.get("linear_story_ids", {}))

    total = 1 + len(stories)
    current = 0

    # --- Project (the epic container) ---
    project_id = state.get("linear_project_id", "")
    if not project_id:
        try:
            analysis = state.get("project_analysis")
            title = getattr(analysis, "project_name", None) or state.get("project_name", "Project")
            description = getattr(analysis, "project_description", None) or ""
            data = _linear_request(
                "mutation($input: ProjectCreateInput!) { projectCreate(input: $input)"
                " { success project { id name url } } }",
                {"input": {"name": title, "description": description, "teamIds": [team["id"]]}},
            )
            payload = data.get("projectCreate", {})
            if not payload.get("success"):
                result.errors.append("Project creation failed: Linear said no.")
                return result, state
            project_id = str(payload["project"]["id"])
            state["linear_project_id"] = project_id
            result.project_id = project_id
            logger.info("Created Linear Project: %s", project_id)
        except Exception as e:
            result.errors.append(f"Project creation failed: {e}")
            return result, state
    else:
        result.project_id = project_id
        result.skipped += 1

    current += 1
    if on_progress:
        on_progress(current, total, "Project ready")

    # Descriptions render against the plan's OWN DoD list and the team's own
    # section headings — the same formatter every tracker uses.
    from yeaboi.agent.state import map_template_headings, resolve_dod_items
    from yeaboi.jira_sync import _format_story_description

    dod_items = resolve_dod_items(state)
    headings = map_template_headings(tuple(state.get("ticket_template_sections") or ()))

    new_story_keys: dict[str, str] = {}
    for story in stories:
        feature = feature_map.get(story.feature_id)
        if story.id in existing_story_keys:
            result.skipped += 1
            current += 1
            if on_progress:
                on_progress(current, total, f"Issue exists: {story.title or story.goal[:40]}")
            continue
        try:
            labels = []
            if feature:
                labels.append(feature.title[:50])
            disc = story.discipline
            labels.append(disc.value if hasattr(disc, "value") else str(disc))
            raw_pri = story.priority
            priority = _PRIORITY_WORD.get(
                (raw_pri.value if hasattr(raw_pri, "value") else str(raw_pri)).lower(), "medium"
            )
            from yeaboi.tools.linear import _PRIORITY_TO_LINEAR

            input_payload: dict = {
                "teamId": team["id"],
                "title": story.title or story.goal,
                "description": _format_story_description(story, feature, dod_items=dod_items, headings=headings),
                "projectId": project_id,
                "priority": _PRIORITY_TO_LINEAR[priority],
            }
            if story.story_points:
                input_payload["estimate"] = int(story.story_points)
            label_ids = _label_ids(team["id"], labels)
            if label_ids:
                input_payload["labelIds"] = label_ids
            data = _linear_request(
                "mutation($input: IssueCreateInput!) { issueCreate(input: $input)"
                " { success issue { id identifier url } } }",
                {"input": input_payload},
            )
            payload = data.get("issueCreate", {})
            if not payload.get("success"):
                raise LinearError("Linear said no")
            issue = payload["issue"]
            new_story_keys[story.id] = str(issue["identifier"])
            issue_ids[story.id] = str(issue["id"])
            result.stories_created[story.id] = str(issue["identifier"])
            logger.info("Created Linear Issue: %s → %s", story.id, issue["identifier"])
            time.sleep(0.1)
        except Exception as e:
            err = f"Story '{story.title or story.id}': {e}"
            logger.error("Linear sync failed — %s", err)
            result.errors.append(err)
        current += 1
        if on_progress:
            on_progress(current, total, f"Issue created: {story.title or story.goal[:40]}")

    state["linear_story_keys"] = {**existing_story_keys, **new_story_keys}
    state["linear_story_ids"] = issue_ids
    return result, state


def sync_tasks_to_linear(
    graph_state: dict[str, Any],
    on_progress: ProgressCallback | None = None,
) -> tuple[LinearSyncResult, dict[str, Any]]:
    """Create sub-issues for each task, cascading to create stories first if needed."""
    from yeaboi.jira_sync import _format_task_description
    from yeaboi.tools.linear import create_sub_issue

    state = dict(graph_state)
    story_keys = state.get("linear_story_keys", {})
    stories = state.get("stories", [])
    if stories and not story_keys:
        story_result, state = sync_stories_to_linear(state, on_progress)
        story_keys = state.get("linear_story_keys", {})
        if story_result.errors and not story_keys:
            return story_result, state

    result = LinearSyncResult(project_id=state.get("linear_project_id"))
    if not is_linear_configured():
        result.errors.append("Linear not configured — missing LINEAR_API_KEY.")
        return result, state

    issue_ids: dict[str, str] = dict(state.get("linear_story_ids", {}))
    tasks = state.get("tasks", [])
    existing_task_keys: dict[str, str] = dict(state.get("linear_task_keys", {}))
    total = len(tasks)
    current = 0
    new_task_keys: dict[str, str] = {}

    for task in tasks:
        if task.id in existing_task_keys:
            result.skipped += 1
            current += 1
            if on_progress:
                on_progress(current, total, f"Sub-issue exists: {task.title[:40]}")
            continue
        parent_uuid = issue_ids.get(task.story_id, "")
        if not parent_uuid:
            result.errors.append(f"Task '{task.title}': its story {task.story_id} has no Linear issue.")
            current += 1
            continue
        try:
            created = create_sub_issue(parent_uuid, task.title, _format_task_description(task))
            new_task_keys[task.id] = str(created["identifier"])
            result.tasks_created[task.id] = str(created["identifier"])
            logger.info("Created Linear sub-issue: %s → %s", task.id, created["identifier"])
            time.sleep(0.1)
        except Exception as e:
            err = f"Task '{task.title or task.id}': {e}"
            logger.error("Linear sync failed — %s", err)
            result.errors.append(err)
        current += 1
        if on_progress:
            on_progress(current, total, f"Sub-issue created: {task.title[:40]}")

    state["linear_task_keys"] = {**existing_task_keys, **new_task_keys}
    return result, state


def sync_cycles_to_linear(
    graph_state: dict[str, Any],
    on_progress: ProgressCallback | None = None,
) -> tuple[LinearSyncResult, dict[str, Any]]:
    """Create Cycles and assign issues, cascading to create stories first if needed.

    Honors the small-project landing decision: ``sprint_target_mode`` "backlog"
    creates nothing (Linear's backlog is "no cycle"), "existing" assigns every
    story to the chosen cycle and never creates one.
    """
    from datetime import timedelta, timezone

    from yeaboi.timeparse import parse_datetime
    from yeaboi.tools.linear import LinearError, _linear_request, _resolve_team, add_issues_to_cycle, fetch_team_cycles

    state = dict(graph_state)
    story_keys = state.get("linear_story_keys", {})
    stories = state.get("stories", [])
    if stories and not story_keys:
        story_result, state = sync_stories_to_linear(state, on_progress)
        story_keys = state.get("linear_story_keys", {})
        if story_result.errors and not story_keys:
            return story_result, state
        result_stories = dict(story_result.stories_created)
        result_story_errors = list(story_result.errors)
    else:
        result_stories = {}
        result_story_errors = []

    result = LinearSyncResult(project_id=state.get("linear_project_id"))
    result.stories_created.update(result_stories)
    result.errors.extend(result_story_errors)

    if state.get("sprint_target_mode") == "backlog":
        logger.info("Cycle sync: backlog mode — issues stay uncycled, no cycle created")
        if on_progress:
            on_progress(1, 1, "Issues left in the backlog — no cycle created")
        return result, state

    if not is_linear_configured():
        result.errors.append("Linear not configured — missing LINEAR_API_KEY.")
        return result, state

    issue_ids: dict[str, str] = dict(state.get("linear_story_ids", {}))
    sprints = state.get("sprints", [])
    existing_cycle_keys: dict[str, str] = dict(state.get("linear_cycle_keys", {}))

    try:
        team = _resolve_team()
        board_cycles = fetch_team_cycles(states=("active", "future", "closed"))
    except LinearError as e:
        result.errors.append(str(e).removeprefix("Error: "))
        return result, state

    open_by_name = {c["name"]: c for c in board_cycles if c["state"] in ("active", "future")}

    if state.get("sprint_target_mode") == "existing":
        return _sync_to_existing_cycle(state, result, story_keys, issue_ids, open_by_name, on_progress)

    sprint_length_weeks = state.get("sprint_length_weeks", 2)
    sprint_start_date_str = state.get("sprint_start_date", "")
    total = len(sprints)
    current = 0
    new_cycle_keys: dict[str, str] = {}

    for idx, sprint in enumerate(sprints):
        if sprint.id in existing_cycle_keys:
            cycle_id = existing_cycle_keys[sprint.id]
            ids = [issue_ids[sid] for sid in sprint.story_ids if sid in issue_ids]
            if ids:
                try:
                    add_issues_to_cycle(cycle_id, ids)
                except Exception as e:
                    logger.warning("Could not update cycle %s issues: %s", sprint.name, e)
            result.skipped += 1
            current += 1
            if on_progress:
                on_progress(current, total, f"Cycle updated: {sprint.name}")
            continue

        progress_label = f"Cycle failed: {sprint.name}"
        try:
            matched = open_by_name.get(sprint.name)
            if matched:
                cycle_id = matched["id"]
                logger.info("Reusing existing Linear Cycle: %s (%s)", sprint.name, cycle_id)
                progress_label = f"Cycle reused: {sprint.name}"
                result.cycles_updated[sprint.id] = cycle_id
            else:
                input_payload: dict = {"teamId": team["id"], "name": sprint.name}
                if sprint_start_date_str:
                    start = parse_datetime(sprint_start_date_str).replace(tzinfo=timezone.utc) + timedelta(
                        weeks=sprint_length_weeks * idx
                    )
                    end = start + timedelta(weeks=sprint_length_weeks) - timedelta(days=1)
                    input_payload["startsAt"] = start.isoformat(timespec="milliseconds")
                    input_payload["endsAt"] = end.isoformat(timespec="milliseconds")
                if getattr(sprint, "goal", ""):
                    input_payload["description"] = sprint.goal
                data = _linear_request(
                    "mutation($input: CycleCreateInput!) { cycleCreate(input: $input) { success cycle { id } } }",
                    {"input": input_payload},
                )
                payload = data.get("cycleCreate", {})
                if not payload.get("success"):
                    raise LinearError("cycles may be disabled for the team — enable them in Linear and retry")
                cycle_id = str(payload["cycle"]["id"])
                logger.info("Created Linear Cycle: %s → %s", sprint.name, cycle_id)
                progress_label = f"Cycle created: {sprint.name}"
                result.cycles_created[sprint.id] = cycle_id

            new_cycle_keys[sprint.id] = cycle_id
            ids = [issue_ids[sid] for sid in sprint.story_ids if sid in issue_ids]
            if ids:
                add_issues_to_cycle(cycle_id, ids)
            time.sleep(0.1)
        except Exception as e:
            err = f"Cycle '{sprint.name}': {e}"
            logger.error("Linear sync failed — %s", err)
            result.errors.append(err)

        current += 1
        if on_progress:
            on_progress(current, total, progress_label)

    state["linear_cycle_keys"] = {**existing_cycle_keys, **new_cycle_keys}
    return result, state


def _sync_to_existing_cycle(
    state: dict[str, Any],
    result: LinearSyncResult,
    story_keys: dict[str, str],
    issue_ids: dict[str, str],
    open_by_name: dict[str, dict],
    on_progress: ProgressCallback | None,
) -> tuple[LinearSyncResult, dict[str, Any]]:
    """Assign the plan's issues to one chosen existing cycle; create nothing.

    The target is resolved by external id when the intake captured it, else by
    name among the open cycles — a closed cycle never matches.
    """
    from yeaboi.tools.linear import add_issues_to_cycle

    sprints = state.get("sprints", [])
    target_name = str(state.get("target_sprint_name") or "")
    target_ext = str(state.get("target_sprint_external_id") or "")
    target = None
    if target_ext:
        target = next((c for c in open_by_name.values() if c["id"] == target_ext), None)
    if target is None and target_name:
        target = open_by_name.get(target_name)
    if target is None:
        result.errors.append(f"No open Linear cycle matches '{target_name or target_ext}'.")
        return result, state

    ids = sorted({issue_ids[sid] for sprint in sprints for sid in sprint.story_ids if sid in issue_ids})
    try:
        add_issues_to_cycle(target["id"], ids)
    except Exception as e:
        result.errors.append(f"Could not add issues to cycle '{target['name']}': {e}")
        return result, state

    merged = dict(state.get("linear_cycle_keys", {}))
    for sprint in sprints:
        merged[sprint.id] = target["id"]
        result.cycles_updated[sprint.id] = target["id"]
    state["linear_cycle_keys"] = merged
    logger.info("Added %d issue(s) to existing cycle %s", len(ids), target["name"])
    if on_progress:
        on_progress(len(sprints), len(sprints), f"Issues added to {target['name']}")
    return result, state


def sync_all_to_linear(
    graph_state: dict[str, Any],
    on_progress: ProgressCallback | None = None,
) -> tuple[LinearSyncResult, dict[str, Any]]:
    """Full sync: Project + Issues + sub-issues + Cycles, aggregating results.

    Returns (aggregated_result, updated_graph_state).
    """
    state = dict(graph_state)
    aggregated = LinearSyncResult()

    story_result, state = sync_stories_to_linear(state, on_progress)
    aggregated.project_id = story_result.project_id
    aggregated.stories_created.update(story_result.stories_created)
    aggregated.errors.extend(story_result.errors)
    aggregated.skipped += story_result.skipped

    if state.get("tasks"):
        task_result, state = sync_tasks_to_linear(state, on_progress)
        aggregated.tasks_created.update(task_result.tasks_created)
        aggregated.errors.extend(task_result.errors)
        aggregated.skipped += task_result.skipped

    if state.get("sprints"):
        cycle_result, state = sync_cycles_to_linear(state, on_progress)
        aggregated.stories_created.update(cycle_result.stories_created)
        aggregated.cycles_created.update(cycle_result.cycles_created)
        aggregated.cycles_updated.update(cycle_result.cycles_updated)
        aggregated.errors.extend(cycle_result.errors)
        aggregated.skipped += cycle_result.skipped

    return aggregated, state
