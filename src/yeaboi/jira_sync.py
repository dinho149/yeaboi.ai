"""Batch Jira creation with idempotency, progress callbacks, and error accumulation.

# See docs: "Tools" — tool types, write tools, human-in-the-loop pattern
#
# This module orchestrates creating Jira resources (Epic, Stories, Sub-tasks, Sprints)
# from the scrum agent's generated artifacts. It is called by the TUI pipeline
# review screens and the project list Jira export button — NOT by the ReAct agent.
#
# Idempotency: each sync function checks the jira_*_keys dicts in graph_state
# before creating anything. Already-created items are skipped. This makes it
# safe to re-run after partial failures.
#
# Semantic mapping:
#   Features → Jira Labels (not separate issues)
#   1 project-level Jira Epic (project name as title)
#   UserStories → Jira Stories linked to the Epic
#   Tasks → Jira Sub-tasks linked to their parent Story
#   Sprints → Jira Sprints with stories assigned
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from jira import JIRAError

from yeaboi.config import get_jira_project_key, get_jira_token

logger = logging.getLogger(__name__)

# Type alias for progress callbacks: (current, total, description)
ProgressCallback = Callable[[int, int, str], None]


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class JiraSyncResult:
    """Accumulates results from a batch Jira sync operation."""

    epic_key: str | None = None
    stories_created: dict[str, str] = field(default_factory=dict)  # internal_id → jira_key
    tasks_created: dict[str, str] = field(default_factory=dict)
    sprints_created: dict[str, str] = field(default_factory=dict)  # internal_id → jira_sprint_id
    errors: list[str] = field(default_factory=list)
    skipped: int = 0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def is_jira_configured() -> bool:
    """Return True if Jira credentials are present in the environment."""
    return get_jira_token() is not None


def _discover_issue_types(jira, project_key: str) -> dict[str, str]:
    """Discover valid issue type names for a Jira project.

    Team-managed (next-gen) projects have project-scoped issue types that
    differ from global types. We query the project's createmeta to get the
    exact names. Common variations:
      - "Story" vs "User Story" vs "Feature"
      - "Sub-task" vs "Subtask"

    Returns a dict with keys "story", "subtask", "epic" mapping to
    the actual issue type name to use for that project.
    """
    defaults = {"story": "Story", "subtask": "Sub-task", "epic": "Epic"}
    try:
        # Use the project statuses endpoint to get project-specific issue types.
        # jira.project(key).issueTypes is the most reliable way to get
        # the issue types that are actually valid for creating issues.
        project = jira.project(project_key)
        type_names: set[str] = set()

        # project.issueTypes is available on the JIRA project resource
        if hasattr(project, "issueTypes"):
            for it in project.issueTypes:
                name = it.name if hasattr(it, "name") else str(it)
                type_names.add(name)

        # Fallback: use createmeta endpoint (works on both project types)
        if not type_names:
            try:
                meta = jira.createmeta(
                    projectKeys=project_key,
                    expand="projects.issuetypes",
                )
                for proj in meta.get("projects", []):
                    for it in proj.get("issuetypes", []):
                        type_names.add(it["name"])
            except Exception:
                pass

        # Last fallback: global issue types
        if not type_names:
            issue_types = jira.issue_types()
            type_names = {it.name for it in issue_types}

        logger.debug("Discovered issue types for %s: %s", project_key, type_names)

        # Find best match for story type — prefer "User Story" over "Story"
        # since team-managed projects commonly use "User Story"
        story_candidates = ["User Story", "Story", "Feature", "Task"]
        for candidate in story_candidates:
            if candidate in type_names:
                defaults["story"] = candidate
                break

        # Find best match for sub-task type
        subtask_candidates = ["Subtask", "Sub-task", "Sub-Task"]
        for candidate in subtask_candidates:
            if candidate in type_names:
                defaults["subtask"] = candidate
                break

        # Epic
        if "Epic" in type_names:
            defaults["epic"] = "Epic"

    except Exception as e:
        logger.warning("Could not discover issue types for %s: %s — using defaults", project_key, e)

    logger.info("Using issue types for %s: %s", project_key, defaults)
    return defaults


def sync_stories_to_jira(
    graph_state: dict[str, Any],
    on_progress: ProgressCallback | None = None,
) -> tuple[JiraSyncResult, dict[str, Any]]:
    """Create a project Epic and Stories in Jira, skipping already-created items.

    Returns (result, updated_graph_state).
    """
    from yeaboi.tools.jira import (
        _create_issue_with_epic_link,
        _jira_error_msg,
        _make_jira_client,
    )

    result = JiraSyncResult()
    state = dict(graph_state)  # shallow copy to avoid mutating caller's dict

    jira = _make_jira_client()
    if jira is None:
        result.errors.append("Jira not configured — missing credentials.")
        return result, state

    project_key = get_jira_project_key() or ""
    if not project_key:
        result.errors.append("JIRA_PROJECT_KEY not set.")
        return result, state

    # Discover valid issue type names for this project (team-managed
    # projects may use different names than company-managed ones).
    issue_types = _discover_issue_types(jira, project_key)

    stories = state.get("stories", [])
    features = state.get("features", [])
    feature_map = {f.id: f for f in features}
    existing_story_keys: dict[str, str] = dict(state.get("jira_story_keys", {}))

    # Total items = 1 (epic) + ALL stories (both new and updates)
    total = 1 + len(stories)
    current = 0

    # --- Epic ---
    epic_key = state.get("jira_epic_key", "")
    if not epic_key:
        try:
            analysis = state.get("project_analysis")
            epic_title = getattr(analysis, "project_name", None) or state.get("project_name", "Project")
            epic_desc = getattr(analysis, "project_description", None) or ""
            fields = {
                "project": {"key": project_key},
                "summary": epic_title,
                "description": epic_desc,
                "issuetype": {"name": issue_types["epic"]},
            }
            issue = jira.create_issue(fields=fields)
            epic_key = issue.key
            state["jira_epic_key"] = epic_key
            result.epic_key = epic_key
            logger.info("Created Jira Epic: %s", epic_key)
        except JIRAError as e:
            result.errors.append(f"Epic creation failed: {_jira_error_msg(e)}")
            return result, state
        except Exception as e:
            result.errors.append(f"Epic creation failed: {e}")
            return result, state
    else:
        result.epic_key = epic_key
        result.skipped += 1

    current += 1
    if on_progress:
        on_progress(current, total, f"Epic: {epic_key}")

    # --- Stories ---
    new_story_keys: dict[str, str] = {}
    # Track the epic link method that works for this project (auto-detected on first story)
    link_method = "auto"

    for story in stories:
        feature = feature_map.get(story.feature_id)

        if story.id in existing_story_keys:
            # Story already exists — update its description (DoD, rationale may have been added)
            jira_key = existing_story_keys[story.id]
            try:
                description = _format_story_description(story, feature)
                jira.issue(jira_key).update(fields={"description": description})
                logger.info("Updated Jira Story description: %s", jira_key)
                time.sleep(0.1)
            except Exception as e:
                logger.warning("Could not update %s: %s", jira_key, e)
            result.skipped += 1
            current += 1
            if on_progress:
                on_progress(current, total, f"Story updated: {story.title or story.goal[:40]}")
            continue

        try:
            labels = []
            if feature:
                labels.append(_feature_title_to_label(feature.title))
            disc = story.discipline
            labels.append(disc.value if hasattr(disc, "value") else str(disc))

            summary = story.title or story.goal
            description = _format_story_description(story, feature)
            raw_pri = story.priority
            priority_name = _map_priority_to_jira(raw_pri.value if hasattr(raw_pri, "value") else str(raw_pri))

            fields: dict = {
                "project": {"key": project_key},
                "summary": summary,
                "description": description,
                "issuetype": {"name": issue_types["story"]},
                "priority": {"name": priority_name},
                "labels": labels,
            }
            if story.story_points:
                fields["customfield_10016"] = int(story.story_points)

            issue, link_field_used = _create_issue_with_epic_link(jira, fields, epic_key, link_method)
            # Cache the link method that worked so subsequent stories don't retry
            if link_method == "auto":
                link_method = "parent" if link_field_used == "parent" else "epic_link"

            new_story_keys[story.id] = issue.key
            result.stories_created[story.id] = issue.key
            logger.info("Created Jira Story: %s → %s", story.id, issue.key)

            # Brief delay to avoid rate limiting
            time.sleep(0.1)
        except JIRAError as e:
            err = f"Story '{story.title or story.id}': {_jira_error_msg(e)}"
            logger.error("Jira sync failed — %s (raw: %s)", err, getattr(e, "text", ""))
            result.errors.append(err)
        except Exception as e:
            err = f"Story '{story.title or story.id}': {e}"
            logger.error("Jira sync failed — %s", err)
            result.errors.append(err)

        current += 1
        if on_progress:
            on_progress(current, total, f"Story created: {story.title or story.goal[:40]}")

    # Merge new keys into state
    merged_story_keys = {**existing_story_keys, **new_story_keys}
    state["jira_story_keys"] = merged_story_keys

    return result, state


def sync_tasks_to_jira(
    graph_state: dict[str, Any],
    on_progress: ProgressCallback | None = None,
) -> tuple[JiraSyncResult, dict[str, Any]]:
    """Create Jira Sub-tasks for each task, cascading to create stories first if needed.

    Returns (result, updated_graph_state).
    """
    from yeaboi.tools.jira import (
        _jira_error_msg,
        _make_jira_client,
        create_subtask,
    )

    state = dict(graph_state)

    # Cascade: create stories first if not done
    story_keys = state.get("jira_story_keys", {})
    stories = state.get("stories", [])
    if stories and not story_keys:
        story_result, state = sync_stories_to_jira(state, on_progress)
        story_keys = state.get("jira_story_keys", {})
        if story_result.errors and not story_keys:
            # Stories failed entirely — can't create tasks
            return story_result, state

    result = JiraSyncResult(epic_key=state.get("jira_epic_key"))
    # Carry over story creation stats from cascade
    result.stories_created = {k: v for k, v in story_keys.items() if k not in graph_state.get("jira_story_keys", {})}

    jira = _make_jira_client()
    if jira is None:
        result.errors.append("Jira not configured — missing credentials.")
        return result, state

    project_key = get_jira_project_key() or ""
    tasks = state.get("tasks", [])
    existing_task_keys: dict[str, str] = dict(state.get("jira_task_keys", {}))

    # Discover valid issue type names for sub-tasks
    issue_types = _discover_issue_types(jira, project_key)

    # Total includes ALL tasks (both new and updates) so the counter is accurate
    total = len(tasks)
    current = 0
    new_task_keys: dict[str, str] = {}

    for task in tasks:
        if task.id in existing_task_keys:
            # Task already exists — update its description (ai_prompt may have been added)
            jira_key = existing_task_keys[task.id]
            try:
                description = _format_task_description(task)
                jira.issue(jira_key).update(fields={"description": description})
                logger.info("Updated Jira Sub-task description: %s", jira_key)
                time.sleep(0.1)
            except Exception as e:
                logger.warning("Could not update %s: %s", jira_key, e)
            result.skipped += 1
            current += 1
            if on_progress:
                on_progress(current, total, f"Subtask updated: {task.title[:40]}")
            continue

        parent_key = story_keys.get(task.story_id)
        if not parent_key:
            result.errors.append(f"Task '{task.title}': parent story '{task.story_id}' not in Jira.")
            current += 1
            if on_progress:
                on_progress(current, total, f"Subtask skipped (no parent): {task.title[:40]}")
            continue

        try:
            description = _format_task_description(task)
            raw_label = getattr(task, "label", None)
            labels = [raw_label.value if hasattr(raw_label, "value") else str(raw_label)] if raw_label else []

            task_key = create_subtask(
                jira,
                summary=task.title,
                parent_key=parent_key,
                description=description,
                project_key=project_key,
                labels=labels,
                issue_type_name=issue_types["subtask"],
            )
            new_task_keys[task.id] = task_key
            result.tasks_created[task.id] = task_key
            logger.info("Created Jira Sub-task: %s → %s", task.id, task_key)
            time.sleep(0.1)
        except JIRAError as e:
            err = f"Task '{task.title}': {_jira_error_msg(e)}"
            logger.error("Jira sync failed — %s", err)
            result.errors.append(err)
        except Exception as e:
            err = f"Task '{task.title}': {e}"
            logger.error("Jira sync failed — %s", err)
            result.errors.append(err)

        current += 1
        if on_progress:
            on_progress(current, total, f"Subtask created: {task.title[:40]}")

    merged_task_keys = {**existing_task_keys, **new_task_keys}
    state["jira_task_keys"] = merged_task_keys

    return result, state


def sync_sprints_to_jira(
    graph_state: dict[str, Any],
    on_progress: ProgressCallback | None = None,
) -> tuple[JiraSyncResult, dict[str, Any]]:
    """Create Jira Sprints and assign stories, cascading to create stories first if needed.

    Returns (result, updated_graph_state).
    """
    from yeaboi.tools.jira import (
        _jira_error_msg,
        _make_jira_client,
        add_issues_to_sprint,
    )

    state = dict(graph_state)

    # Cascade: create stories first if not done
    story_keys = state.get("jira_story_keys", {})
    stories = state.get("stories", [])
    if stories and not story_keys:
        story_result, state = sync_stories_to_jira(state, on_progress)
        story_keys = state.get("jira_story_keys", {})
        if story_result.errors and not story_keys:
            return story_result, state

    result = JiraSyncResult(epic_key=state.get("jira_epic_key"))

    jira = _make_jira_client()
    if jira is None:
        result.errors.append("Jira not configured — missing credentials.")
        return result, state

    project_key = get_jira_project_key() or ""
    if not project_key:
        result.errors.append("JIRA_PROJECT_KEY not set.")
        return result, state

    sprints = state.get("sprints", [])
    existing_sprint_keys: dict[str, str] = dict(state.get("jira_sprint_keys", {}))

    # Discover board ID
    try:
        boards = jira.boards(projectKeyOrID=project_key)
        if not boards:
            result.errors.append(f"No Jira board found for project '{project_key}'.")
            return result, state
        board_id = boards[0].id
    except JIRAError as e:
        result.errors.append(f"Board discovery failed: {_jira_error_msg(e)}")
        return result, state

    # Fetch all existing sprints on the board so we can match by name
    # and reuse instead of always creating new ones.
    existing_board_sprints: dict[str, int] = {}  # name → sprint_id
    try:
        for sprint_state in ("future", "active", "closed"):
            try:
                board_sprints = jira.sprints(board_id, state=sprint_state)
                for bs in board_sprints:
                    existing_board_sprints[bs.name] = bs.id
            except JIRAError:
                pass  # some states may not be available
        logger.debug("Found %d existing sprints on board %s", len(existing_board_sprints), board_id)
    except Exception as e:
        logger.warning("Could not fetch existing sprints: %s — will create new ones", e)

    # Detect the board's sprint naming pattern (e.g. "PSOT Sprint {N}") so we
    # can rename LLM-generated sprint names to match the convention.
    # The LLM may generate "Sprint 1", "Sprint 2" but the board uses
    # "PSOT Sprint 107", "PSOT Sprint 108".
    sprint_name_prefix = ""
    max_existing_number = 0
    for name in existing_board_sprints:
        match = re.match(r"^(.+?)(\d+)\s*$", name)
        if match:
            prefix_candidate = match.group(1)
            num = int(match.group(2))
            if num > max_existing_number:
                max_existing_number = num
                sprint_name_prefix = prefix_candidate
    if sprint_name_prefix:
        logger.debug(
            "Detected sprint naming pattern: '%sN' (max existing: %d)",
            sprint_name_prefix,
            max_existing_number,
        )

    # Determine the starting sprint number for new sprints.
    # Use the state's starting_sprint_number if set, otherwise increment
    # from the highest existing sprint number on the board.
    starting_number = state.get("starting_sprint_number", 0)
    if not starting_number and max_existing_number > 0:
        starting_number = max_existing_number + 1

    sprint_length_weeks = state.get("sprint_length_weeks", 2)
    sprint_start_date_str = state.get("sprint_start_date", "")

    total = len(sprints)
    current = 0
    new_sprint_keys: dict[str, str] = {}

    for idx, sprint in enumerate(sprints):
        # Normalize sprint name to match the board's naming convention.
        # E.g. LLM generates "Sprint 2" → rename to "PSOT Sprint 108"
        sprint_name = sprint.name
        if sprint_name_prefix and starting_number:
            sprint_number = starting_number + idx
            sprint_name = f"{sprint_name_prefix}{sprint_number}"
            if sprint_name != sprint.name:
                logger.info("Renamed sprint '%s' → '%s' (board convention)", sprint.name, sprint_name)
        if sprint.id in existing_sprint_keys:
            # Already tracked in state — just assign stories (in case new ones were added)
            existing_jira_id = int(existing_sprint_keys[sprint.id])
            issue_keys = [story_keys[sid] for sid in sprint.story_ids if sid in story_keys]
            if issue_keys:
                try:
                    add_issues_to_sprint(jira, existing_jira_id, issue_keys)
                except Exception as e:
                    logger.warning("Could not update sprint %s issues: %s", sprint_name, e)
            result.skipped += 1
            current += 1
            if on_progress:
                on_progress(current, total, f"Sprint updated: {sprint_name}")
            continue

        try:
            # Check if a sprint with this name already exists on the board
            existing_jira_sprint_id = existing_board_sprints.get(sprint_name)

            if existing_jira_sprint_id:
                # Sprint exists — reuse it, just assign stories
                sprint_id_str = str(existing_jira_sprint_id)
                logger.info("Reusing existing Jira Sprint: %s (ID: %s)", sprint_name, sprint_id_str)
                progress_label = f"Sprint reused: {sprint_name}"
            else:
                # Sprint doesn't exist — create it
                kwargs: dict[str, Any] = {"name": sprint_name, "board_id": board_id}
                if sprint.goal:
                    kwargs["goal"] = sprint.goal
                if sprint_start_date_str:
                    start = datetime.fromisoformat(sprint_start_date_str) + timedelta(weeks=sprint_length_weeks * idx)
                    end = start + timedelta(weeks=sprint_length_weeks)
                    kwargs["startDate"] = start.strftime("%Y-%m-%d")
                    kwargs["endDate"] = end.strftime("%Y-%m-%d")

                jira_sprint = jira.create_sprint(**kwargs)
                sprint_id_str = str(jira_sprint.id)
                existing_jira_sprint_id = int(jira_sprint.id)
                logger.info("Created Jira Sprint: %s → %s", sprint_name, sprint_id_str)
                progress_label = f"Sprint created: {sprint_name}"

            new_sprint_keys[sprint.id] = sprint_id_str
            result.sprints_created[sprint.id] = sprint_id_str

            # Assign stories to sprint (whether new or existing)
            issue_keys = [story_keys[sid] for sid in sprint.story_ids if sid in story_keys]
            if issue_keys:
                add_issues_to_sprint(jira, existing_jira_sprint_id, issue_keys)

            time.sleep(0.1)
        except JIRAError as e:
            err = f"Sprint '{sprint_name}': {_jira_error_msg(e)}"
            logger.error("Jira sync failed — %s", err)
            result.errors.append(err)
            progress_label = f"Sprint failed: {sprint_name}"
        except Exception as e:
            err = f"Sprint '{sprint_name}': {e}"
            logger.error("Jira sync failed — %s", err)
            result.errors.append(err)
            progress_label = f"Sprint failed: {sprint_name}"

        current += 1
        if on_progress:
            on_progress(current, total, progress_label)

    merged_sprint_keys = {**existing_sprint_keys, **new_sprint_keys}
    state["jira_sprint_keys"] = merged_sprint_keys

    return result, state


def sync_all_to_jira(
    graph_state: dict[str, Any],
    on_progress: ProgressCallback | None = None,
) -> tuple[JiraSyncResult, dict[str, Any]]:
    """Full sync: Epic + Stories + Tasks + Sprints, aggregating results.

    Returns (aggregated_result, updated_graph_state).
    """
    state = dict(graph_state)
    aggregated = JiraSyncResult()

    # Stories (includes Epic creation)
    story_result, state = sync_stories_to_jira(state, on_progress)
    aggregated.epic_key = story_result.epic_key
    aggregated.stories_created.update(story_result.stories_created)
    aggregated.errors.extend(story_result.errors)
    aggregated.skipped += story_result.skipped

    # Tasks
    if state.get("tasks"):
        task_result, state = sync_tasks_to_jira(state, on_progress)
        aggregated.tasks_created.update(task_result.tasks_created)
        aggregated.errors.extend(task_result.errors)
        aggregated.skipped += task_result.skipped

    # Sprints
    if state.get("sprints"):
        sprint_result, state = sync_sprints_to_jira(state, on_progress)
        aggregated.sprints_created.update(sprint_result.stories_created)
        aggregated.sprints_created.update(sprint_result.sprints_created)
        aggregated.errors.extend(sprint_result.errors)
        aggregated.skipped += sprint_result.skipped

    return aggregated, state


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


# Map internal Priority enum values to Jira Cloud priority names.
# Jira Cloud standard priorities: Highest, High, Medium, Low, Lowest.
# Our enum: critical, high, medium, low.
_PRIORITY_TO_JIRA: dict[str, str] = {
    "critical": "Highest",
    "high": "High",
    "medium": "Medium",
    "low": "Low",
}


def _map_priority_to_jira(priority_value: str) -> str:
    """Map an internal priority string to a Jira-compatible priority name."""
    return _PRIORITY_TO_JIRA.get(priority_value, "Medium")


def _feature_title_to_label(title: str) -> str:
    """Sanitize a feature title for use as a Jira label.

    Jira labels cannot contain spaces. Replace spaces with hyphens,
    strip special characters, and limit length.
    """
    if not title:
        return "Feature"
    # Replace whitespace with hyphens, strip non-alphanumeric except hyphens
    label = re.sub(r"\s+", "-", title.strip())
    label = re.sub(r"[^a-zA-Z0-9\-]", "", label)
    # Collapse multiple hyphens and strip leading/trailing hyphens
    label = re.sub(r"-{2,}", "-", label).strip("-")
    return label[:50] or "Feature"


def _format_story_description(story, feature=None) -> str:
    """Format a UserStory as a Jira description with acceptance criteria, DoD, and rationale."""
    from yeaboi.agent.state import DOD_ITEMS

    lines: list[str] = []

    # User story sentence
    lines.append(f"*As a* {story.persona}, *I want to* {story.goal}, *so that* {story.benefit}.")
    lines.append("")

    # Acceptance criteria
    if story.acceptance_criteria:
        lines.append("h3. Acceptance Criteria")
        for i, ac in enumerate(story.acceptance_criteria, 1):
            lines.append(f"# *AC{i}*")
            lines.append(f"*Given* {ac.given}")
            lines.append(f"*When* {ac.when}")
            lines.append(f"*Then* {ac.then}")
            lines.append("")

    # Definition of Done — checkboxes with applicable/N/A items
    dod = getattr(story, "dod_applicable", None)
    if dod and len(dod) == len(DOD_ITEMS):
        lines.append("h3. Definition of Done")
        for item, applicable in zip(DOD_ITEMS, dod):
            if applicable:
                lines.append(f"* [x] {item}")
            else:
                lines.append(f"* [ ] ~{item}~")
        lines.append("")

    # Points rationale
    rationale = getattr(story, "points_rationale", "")
    if rationale:
        lines.append("h3. Points Rationale")
        lines.append(rationale)
        lines.append("")

    # Feature context
    if feature:
        lines.append(f"_Feature: {feature.title}_")

    return "\n".join(lines)


def _format_task_description(task) -> str:
    """Format a Task as a Jira sub-task description."""
    lines: list[str] = []
    if task.description:
        lines.append(task.description)

    if hasattr(task, "test_plan") and task.test_plan:
        lines.append("")
        lines.append("h3. Test Plan")
        lines.append(task.test_plan)

    if hasattr(task, "ai_prompt") and task.ai_prompt:
        lines.append("")
        lines.append("h3. AI Prompt")
        lines.append(task.ai_prompt)

    return "\n".join(lines)
